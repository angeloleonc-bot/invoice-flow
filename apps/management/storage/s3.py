from functools import lru_cache

import boto3
from botocore.config import Config
from django.conf import settings


@lru_cache(maxsize=1)
def get_s3_client():
    client_kwargs = {
        "service_name": "s3",
        "region_name": settings.AWS_S3_REGION_NAME,
        "config": Config(
            signature_version="s3v4",
            retries={
                "max_attempts": 3,
                "mode": "standard",
            },
        ),
    }

    if settings.AWS_ACCESS_KEY_ID:
        client_kwargs["aws_access_key_id"] = settings.AWS_ACCESS_KEY_ID

    if settings.AWS_SECRET_ACCESS_KEY:
        client_kwargs["aws_secret_access_key"] = (
            settings.AWS_SECRET_ACCESS_KEY
        )

    session_token = getattr(settings, "AWS_SESSION_TOKEN", None)

    if session_token:
        client_kwargs["aws_session_token"] = session_token

    return boto3.client(**client_kwargs)