from google.cloud import storage
import sys


def cors_configuration(bucket_name: str):
    """Set CORS policy for the given GCS bucket to allow localhost and trilogydata.dev."""
    storage_client = storage.Client()
    bucket = storage_client.get_bucket(bucket_name)

    cors_config = [
        {
            "origin": [
                "http://localhost:3000",
                "http://localhost:5173",
                "https://trilogydata.dev"
            ],
            "responseHeader": [
                "Content-Type",
                "x-goog-resumable"
            ],
            "method": [
                "GET",
                "HEAD",
                "PUT",
                "POST"
            ],
            "maxAgeSeconds": 3600
        }
    ]

    bucket.cors = cors_config
    bucket.patch()

    print(f"✅ CORS policy set on bucket '{bucket.name}':")
    for rule in bucket.cors:
        print(rule)


if __name__ == "__main__":
    cors_configuration('trilogy_public_geo_data')
