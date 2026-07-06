#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-/home/ajsmgpt/AJSMGPT_git}"
DEPLOY_ROOT="${DEPLOY_ROOT:-/home/ajsmgpt/AJSMGPT}"
PYTHON_BIN="${PYTHON_BIN:-/home/ajsmgpt/AJSMGPT/venv/bin/python3}"

RASA_DIR="$PROJECT_ROOT/AutomateQuery/NLP/rasa_nlu"
GENERATOR="$PROJECT_ROOT/AutomateQuery/NLP/training_generator/generate_rasa_nlu.py"
RASA_IMAGE="${RASA_IMAGE:-rasa/rasa:3.6.21-full}"
RASA_CONTAINER="${RASA_CONTAINER:-ajsm_rasa_nlu}"
DUCKLING_CONTAINER="${DUCKLING_CONTAINER:-ajsm_duckling}"

MAX_PER_INTENT="${MAX_PER_INTENT:-80}"
RUN_DBFREE_REGRESSION="${RUN_DBFREE_REGRESSION:-1}"

echo "===================================================================================================="
echo "AJSMGPT RASA NLU RETRAIN PIPELINE"
echo "===================================================================================================="
echo "PROJECT_ROOT: $PROJECT_ROOT"
echo "DEPLOY_ROOT: $DEPLOY_ROOT"
echo "PYTHON_BIN: $PYTHON_BIN"
echo "RASA_DIR: $RASA_DIR"
echo "MAX_PER_INTENT: $MAX_PER_INTENT"
echo "RUN_DBFREE_REGRESSION: $RUN_DBFREE_REGRESSION"
echo

cd "$PROJECT_ROOT"

echo "----------------------------------------------------------------------------------------------------"
echo "1. Checking required files"
echo "----------------------------------------------------------------------------------------------------"
test -f "$GENERATOR"
test -f "$RASA_DIR/config.yml"
test -f "$RASA_DIR/domain.yml"
test -f "$PROJECT_ROOT/app/rasa_nlu_client.py"
test -f "$PROJECT_ROOT/app/query_planner_observe.py"

echo "OK: required files exist"
echo

echo "----------------------------------------------------------------------------------------------------"
echo "2. Checking Duckling container"
echo "----------------------------------------------------------------------------------------------------"
if ! docker ps --format '{{.Names}}' | grep -qx "$DUCKLING_CONTAINER"; then
  echo "Duckling container not running. Starting $DUCKLING_CONTAINER..."
  docker rm -f "$DUCKLING_CONTAINER" 2>/dev/null || true
  docker run -d \
    --name "$DUCKLING_CONTAINER" \
    --restart unless-stopped \
    -p 127.0.0.1:8001:8000 \
    rasa/duckling
  sleep 5
else
  echo "OK: Duckling already running"
fi

curl -s -X POST http://127.0.0.1:8001/parse \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data "locale=en_GB&text=last 3 purchases in 2025" >/tmp/ajsm_duckling_test.json

echo "OK: Duckling parse endpoint reachable"
echo

echo "----------------------------------------------------------------------------------------------------"
echo "3. Generate Rasa NLU training data"
echo "----------------------------------------------------------------------------------------------------"
"$PYTHON_BIN" "$GENERATOR" --max-per-intent "$MAX_PER_INTENT"

echo
echo "Generated data files:"
ls -lh "$RASA_DIR/data/"
echo

echo "----------------------------------------------------------------------------------------------------"
echo "4. Validate Rasa data"
echo "----------------------------------------------------------------------------------------------------"
cd "$RASA_DIR"

docker run --rm \
  --user "$(id -u):$(id -g)" \
  -e HOME=/tmp \
  --add-host=host.docker.internal:host-gateway \
  -v "$PWD:/app" \
  "$RASA_IMAGE" \
  data validate

echo
echo "OK: Rasa data validation completed"
echo

echo "----------------------------------------------------------------------------------------------------"
echo "5. Train Rasa NLU"
echo "----------------------------------------------------------------------------------------------------"
docker run --rm \
  --user "$(id -u):$(id -g)" \
  -e HOME=/tmp \
  --add-host=host.docker.internal:host-gateway \
  -v "$PWD:/app" \
  "$RASA_IMAGE" \
  train nlu

MODEL="$(ls -t "$RASA_DIR"/models/*.tar.gz | head -1)"
echo
echo "Latest model: $MODEL"
echo

echo "----------------------------------------------------------------------------------------------------"
echo "6. Restart Rasa NLU server"
echo "----------------------------------------------------------------------------------------------------"
docker rm -f "$RASA_CONTAINER" 2>/dev/null || true

docker run -d \
  --name "$RASA_CONTAINER" \
  --restart unless-stopped \
  --user "$(id -u):$(id -g)" \
  -e HOME=/tmp \
  --add-host=host.docker.internal:host-gateway \
  -p 127.0.0.1:5005:5005 \
  -v "$RASA_DIR:/app" \
  "$RASA_IMAGE" \
  run \
  --enable-api \
  --model "/app/models/$(basename "$MODEL")" \
  --port 5005 \
  -i 0.0.0.0

echo "Waiting for Rasa server..."
READY=0
for i in $(seq 1 40); do
  if curl -s http://127.0.0.1:5005/model/parse \
    -H "Content-Type: application/json" \
    -d '{"text":"last supplier for mouse"}' | grep -q "purchase_last_supplier_by_material"; then
    READY=1
    break
  fi
  sleep 2
done

if [ "$READY" != "1" ]; then
  echo "ERROR: Rasa server did not become ready"
  docker logs --tail 120 "$RASA_CONTAINER" || true
  exit 1
fi

echo "OK: Rasa server ready"
echo

echo "----------------------------------------------------------------------------------------------------"
echo "7. Run adapter test"
echo "----------------------------------------------------------------------------------------------------"
cd "$PROJECT_ROOT"
"$PYTHON_BIN" AutomateQuery/tests/test_rasa_nlu_adapter.py

echo
echo "----------------------------------------------------------------------------------------------------"
echo "8. Run Rasa vs planner observe test"
echo "----------------------------------------------------------------------------------------------------"
"$PYTHON_BIN" AutomateQuery/tests/test_rasa_vs_planner_observe.py

if [ "$RUN_DBFREE_REGRESSION" = "1" ]; then
  echo
  echo "----------------------------------------------------------------------------------------------------"
  echo "9. Run DB-free regression"
  echo "----------------------------------------------------------------------------------------------------"
  AJSMGPT_PROJECT_ROOT="$DEPLOY_ROOT" \
  "$PYTHON_BIN" AutomateQuery/tests/run_regression_checks.py --mode dbfree
else
  echo
  echo "Skipping DB-free regression because RUN_DBFREE_REGRESSION=$RUN_DBFREE_REGRESSION"
fi

echo
echo "===================================================================================================="
echo "RASA NLU RETRAIN PIPELINE COMPLETE"
echo "===================================================================================================="
echo "Latest model: $MODEL"
echo
echo "Important:"
echo "- Commit source/training YAML if changed."
echo "- Do not commit AutomateQuery/NLP/rasa_nlu/models/"
echo "- Do not commit AutomateQuery/NLP/rasa_nlu/.rasa/"
