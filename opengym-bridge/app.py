import io
import os
import re
import logging
from datetime import datetime
import requests
from requests.auth import HTTPBasicAuth
from fastapi import FastAPI, Request, HTTPException

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("opengym-bridge")

app = FastAPI(title="openGym to Intervals & Strava Bridge")

INTERVALS_ATHLETE_ID = os.getenv("INTERVALS_ATHLETE_ID")
INTERVALS_API_KEY = os.getenv("INTERVALS_API_KEY")
DEFAULT_RPE = int(os.getenv("DEFAULT_STRENGTH_RPE", "7"))

STRAVA_CLIENT_ID = os.getenv("STRAVA_CLIENT_ID")
STRAVA_CLIENT_SECRET = os.getenv("STRAVA_CLIENT_SECRET")
STRAVA_REFRESH_TOKEN = os.getenv("STRAVA_REFRESH_TOKEN")

def get_strava_access_token():
    if not (STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET and STRAVA_REFRESH_TOKEN):
        return None
    try:
        resp = requests.post(
            "https://www.strava.com/oauth/token",
            data={
                "client_id": STRAVA_CLIENT_ID,
                "client_secret": STRAVA_CLIENT_SECRET,
                "refresh_token": STRAVA_REFRESH_TOKEN,
                "grant_type": "refresh_token"
            },
            timeout=10
        )
        if resp.status_code == 200:
            return resp.json().get("access_token")
        logger.error(f"Erro ao renovar token do Strava: {resp.text}")
    except Exception as e:
        logger.error(f"Falha de conexao com Strava OAuth: {e}")
    return None

def create_strava_activity(name: str, start_dt: datetime, duration_secs: int, description: str):
    token = get_strava_access_token()
    if not token:
        logger.warning("Credenciais do Strava ausentes ou invalidas. Pulando envio.")
        return None

    # Remove Markdown (** e espacos duplos) para exibicao limpa no Strava
    clean_desc = re.sub(r"\*\*(.*?)\*\*", r"\1", description).replace("  \n", "\n")

    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "name": name,
        "sport_type": "WeightTraining",
        "type": "WeightTraining",
        "start_date_local": start_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "elapsed_time": max(60, duration_secs),
        "description": clean_desc,
        "trainer": 1
    }

    try:
        resp = requests.post(
            "https://www.strava.com/api/v3/activities",
            data=payload,
            headers=headers,
            timeout=15
        )
        if resp.status_code in [200, 201]:
            activity = resp.json()
            activity_id = activity.get("id")
            logger.info(f"Atividade criada com sucesso no Strava! ID: {activity_id}")
            return activity
        else:
            logger.error(f"Erro ao criar atividade no Strava: {resp.status_code} - {resp.text}")
    except Exception as e:
        logger.error(f"Erro ao conectar com API do Strava: {e}")
    return None

@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "athlete_id_set": bool(INTERVALS_ATHLETE_ID),
        "strava_configured": bool(STRAVA_CLIENT_ID and STRAVA_REFRESH_TOKEN)
    }

@app.post("/webhook")
async def receive_opengym_webhook(request: Request):
    try:
        raw_payload = await request.json()
    except Exception as e:
        logger.error(f"Erro ao ler JSON: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    logger.info(f"Payload recebido: {raw_payload}")

    workout = raw_payload.get("data", {}).get("workout", raw_payload)

    workout_name = workout.get("name") or "Treino de Força (openGym)"
    started_at_str = workout.get("startedAt") or workout.get("date")
    ended_at_str = workout.get("endedAt")
    
    if started_at_str:
        try:
            start_dt = datetime.fromisoformat(started_at_str.replace("Z", "+00:00"))
        except Exception:
            start_dt = datetime.now()
    else:
        start_dt = datetime.now()

    if started_at_str and ended_at_str:
        try:
            end_dt = datetime.fromisoformat(ended_at_str.replace("Z", "+00:00"))
            duration_secs = int((end_dt - start_dt).total_seconds())
        except Exception:
            duration_secs = int(workout.get("durationMin", 4) * 60)
    else:
        duration_secs = int(workout.get("durationMin", 4) * 60)

    duration_secs = max(10, duration_secs)
    duration_mins = max(1, round(duration_secs / 60))

    description_lines = []
    exercises = workout.get("exercises") or []
    collected_rpes = []

    for ex in exercises:
        ex_name = ex.get("name") or "Exercício"
        description_lines.append(f"**{ex_name.title()}**")
        sets = ex.get("sets") or []
        for idx, s in enumerate(sets, 1):
            reps = s.get("reps", 0)
            weight = s.get("weight", 0)
            rpe = s.get("rpe")
            if rpe:
                try:
                    collected_rpes.append(float(rpe))
                except ValueError:
                    pass
            set_str = f"- Set {idx}: {reps} reps @ {weight}kg"
            if rpe:
                set_str += f" (RPE {rpe})"
            description_lines.append(f"{set_str}  ")
        description_lines.append("")

    session_rpe = round(sum(collected_rpes) / len(collected_rpes)) if collected_rpes else DEFAULT_RPE
    calculated_load = max(1, round((duration_mins * session_rpe / 60) * 50))

    notes = workout.get("notes") or workout.get("sessionNote")
    if notes:
        description_lines.append(f"**Notas:** {notes}")

    full_description = "\n".join(description_lines).strip()

    # 1. Intervals.icu (mantem Markdown para renderizacao rica)
    iso_start = start_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    tcx_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">
  <Activities>
    <Activity Sport="WeightTraining">
      <Id>{iso_start}</Id>
      <Lap StartTime="{iso_start}">
        <TotalTimeSeconds>{duration_secs}</TotalTimeSeconds>
        <DistanceMeters>0.0</DistanceMeters>
        <Calories>0</Calories>
        <Intensity>Active</Intensity>
        <TriggerMethod>Manual</TriggerMethod>
        <Track>
          <Trackpoint>
            <Time>{iso_start}</Time>
          </Trackpoint>
        </Track>
      </Lap>
      <Notes>{workout_name}</Notes>
    </Activity>
  </Activities>
</TrainingCenterDatabase>
"""
    tcx_bytes = tcx_content.encode("utf-8")
    url_upload = f"https://intervals.icu/api/v1/athlete/{INTERVALS_ATHLETE_ID}/activities"
    auth = HTTPBasicAuth("API_KEY", INTERVALS_API_KEY)
    files = {"file": ("workout.tcx", io.BytesIO(tcx_bytes), "application/vnd.garmin.tcx+xml")}

    intervals_id = None
    try:
        upload_resp = requests.post(url_upload, files=files, auth=auth, timeout=20)
        if upload_resp.status_code in [200, 201]:
            activity_data = upload_resp.json()
            intervals_id = activity_data.get("id")
            
            update_url = f"https://intervals.icu/api/v1/activity/{intervals_id}"
            update_payload = {
                "name": workout_name,
                "description": full_description,
                "icu_training_load": calculated_load,
                "perceived_exertion": session_rpe
            }
            requests.put(update_url, json=update_payload, auth=auth, timeout=15)
            logger.info(f"Intervals.icu: Atividade {intervals_id} sincronizada.")
    except Exception as e:
        logger.error(f"Erro ao sincronizar com Intervals: {e}")

    # 2. Strava (versao com texto plano limpo)
    strava_act = create_strava_activity(workout_name, start_dt, duration_secs, full_description)
    strava_id = strava_act.get("id") if strava_act else None

    return {
        "status": "success",
        "intervals_id": intervals_id,
        "strava_activity_id": strava_id,
        "load": calculated_load
    }