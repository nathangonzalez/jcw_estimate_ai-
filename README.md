# Construction Estimator

Prototype estimating service with a small Flask API and examples for
integrating Firebase and Vertex AI.

## Running locally

```bash
pip install -r requirements.txt
python app.py
```

The service exposes a `/healthz` endpoint used by tests and a simple
`/api/estimate` endpoint that multiplies room areas by cost-per-square-foot
values.

## Firebase + Vertex AI demo

`vertex_firebase_demo.py` demonstrates how to initialize Firebase and call a
Vertex AI text model. Set the following environment variables before
running the script:

- `FIREBASE_CREDENTIALS`: Path to a Firebase service account JSON file.
- `GCP_PROJECT`: Google Cloud project ID.
- `GCP_REGION` (optional): Vertex AI region, defaults to `us-central1`.

The script generates an example estimate and stores it in Firestore.

## CI pipeline

GitHub Actions workflow in `.github/workflows/ci.yml` installs dependencies,
configures the Google Cloud SDK, and runs the test suite with `pytest`.
Add `GCP_PROJECT` and `GCP_SA_KEY` secrets to enable gcloud access in CI.
