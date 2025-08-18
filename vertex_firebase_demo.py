"""Minimal demo wiring Firebase with Vertex AI.

This script initializes Firebase and Vertex AI clients using
service account credentials. It then generates a text estimate via
Vertex AI's text model and stores the result in a Firestore collection.

Environment variables expected:
- FIREBASE_CREDENTIALS: Path to Firebase service account key JSON.
- GCP_PROJECT: Google Cloud project ID.
- GCP_REGION: (optional) Region for Vertex AI, defaults to us-central1.
"""
import os
from typing import Optional

from firebase_admin import credentials, firestore, initialize_app
from google.cloud import aiplatform


def init_services() -> None:
    """Initialize Firebase and Vertex AI SDKs."""
    cred_path: Optional[str] = os.environ.get("FIREBASE_CREDENTIALS")
    project_id: Optional[str] = os.environ.get("GCP_PROJECT")
    region: str = os.environ.get("GCP_REGION", "us-central1")

    if not project_id:
        raise EnvironmentError("GCP_PROJECT env var required")

    if cred_path:
        cred = credentials.Certificate(cred_path)
        initialize_app(cred)
    else:
        initialize_app()

    aiplatform.init(project=project_id, location=region)


def generate_estimate(prompt: str) -> str:
    """Call a Vertex AI text model and return the generated text."""
    model = aiplatform.TextGenerationModel.from_pretrained("text-bison@002")
    response = model.predict(prompt)
    return response.text


def save_estimate(doc_id: str, content: str) -> None:
    """Save the generated content to Firestore."""
    db = firestore.client()
    db.collection("estimates").document(doc_id).set({"content": content})


if __name__ == "__main__":
    init_services()
    estimate = generate_estimate("Estimate cost for a 2,000 sq ft house")
    save_estimate("demo", estimate)
    print("Saved estimate:", estimate)
