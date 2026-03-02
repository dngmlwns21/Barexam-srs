#!/bin/bash
# 감자 팩토리 — GCP Cloud Run 최초 배포 스크립트
# 실행: chmod +x deploy_gcp.sh && ./deploy_gcp.sh

set -e

PROJECT_ID="barexam-srs"
SERVICE_NAME="gamzafactory"
REGION="asia-northeast3"  # 서울 리전

echo "1. gcloud 로그인 확인..."
gcloud auth print-access-token > /dev/null || gcloud auth login

echo "2. 프로젝트 설정..."
gcloud config set project $PROJECT_ID

echo "3. 필요한 API 활성화..."
gcloud services enable run.googleapis.com containerregistry.googleapis.com cloudbuild.googleapis.com

echo "4. 이미지 빌드 & 푸시..."
gcloud builds submit --tag gcr.io/$PROJECT_ID/$SERVICE_NAME .

echo "5. Cloud Run 배포..."
gcloud run deploy $SERVICE_NAME \
  --image gcr.io/$PROJECT_ID/$SERVICE_NAME \
  --region $REGION \
  --platform managed \
  --allow-unauthenticated \
  --port 8000 \
  --memory 512Mi \
  --cpu 1 \
  --min-instances 0 \
  --max-instances 10 \
  --timeout 300 \
  --set-env-vars "ALGORITHM=HS256,ACCESS_TOKEN_EXPIRE_MINUTES=1440,REFRESH_TOKEN_EXPIRE_DAYS=30"

SERVICE_URL=$(gcloud run services describe $SERVICE_NAME --region $REGION --format='value(status.url)')
echo ""
echo "배포 완료! 서비스 URL: $SERVICE_URL"
echo ""
echo "⚠️  다음 환경변수를 GCP Console에서 반드시 설정하세요:"
echo "   DATABASE_URL       = Neon PostgreSQL URL"
echo "   SECRET_KEY         = 현재 .env의 SECRET_KEY 값"
echo "   ANTHROPIC_API_KEY  = Anthropic API 키"
echo "   EXTRA_ALLOWED_ORIGINS = $SERVICE_URL"
