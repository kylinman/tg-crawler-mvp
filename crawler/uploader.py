import os
import mimetypes
import boto3
from botocore.exceptions import ClientError
from pathlib import Path
from PIL import Image
import io
import json

class MinIOUploader:
    def __init__(self):
        self.client = boto3.client(
            's3',
            endpoint_url=os.getenv('S3_ENDPOINT', 'http://localhost:9000'),
            aws_access_key_id=os.getenv('S3_ACCESS_KEY', 'minioadmin'),
            aws_secret_access_key=os.getenv('S3_SECRET_KEY', 'minioadmin'),
            region_name='us-east-1'
        )
        self.bucket = os.getenv('S3_BUCKET', 'tg-media')
        self.public_endpoint = os.getenv('S3_PUBLIC_ENDPOINT', os.getenv('S3_ENDPOINT', 'http://localhost:9000')).rstrip('/')

        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError:
            self.client.create_bucket(Bucket=self.bucket)
            self.client.put_bucket_policy(
                Bucket=self.bucket,
                Policy=json.dumps({
                    "Version": "2012-10-17",
                    "Statement": [{
                        "Effect": "Allow",
                        "Principal": "*",
                        "Action": ["s3:GetObject"],
                        "Resource": f"arn:aws:s3:::{self.bucket}/*"
                    }]
                })
            )

    def upload_media(self, local_path: str, channel: str, message_id: int, seq: int = 0) -> dict:
        ext = Path(local_path).suffix or '.bin'
        date_folder = __import__('datetime').datetime.now().strftime('%Y/%m/%d')
        s3_key = f"{channel}/{date_folder}/{message_id}_{seq}{ext}"
        content_type = mimetypes.guess_type(local_path)[0] or 'application/octet-stream'

        self.client.upload_file(
            local_path, self.bucket, s3_key,
            ExtraArgs={'ContentType': content_type}
        )

        thumb_key = None
        if content_type.startswith('image/'):
            thumb_key = f"{channel}/{date_folder}/{message_id}_{seq}_thumb.jpg"
            try:
                with Image.open(local_path) as img:
                    img.thumbnail((400, 400))
                    thumb_buffer = io.BytesIO()
                    img.save(thumb_buffer, format='JPEG', quality=75)
                    thumb_buffer.seek(0)
                    self.client.put_object(
                        Bucket=self.bucket, Key=thumb_key,
                        Body=thumb_buffer, ContentType='image/jpeg'
                    )
            except Exception as e:
                print(f"Thumb fail {local_path}: {e}")
                thumb_key = None

        base = self.public_endpoint
        return {
            's3_key': s3_key,
            's3_url': f"{base}/{self.bucket}/{s3_key}",
            'thumb_key': thumb_key,
            'thumb_url': f"{base}/{self.bucket}/{thumb_key}" if thumb_key else None,
        }

    def upload_photo(self, local_path: str, channel: str, message_id: int, seq: int = 0) -> dict:
        return self.upload_media(local_path, channel, message_id, seq)
