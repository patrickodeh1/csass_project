#!/bin/bash

# Cloud Run Deployment Setup Script for CSASS Project
# Run this script once to set up all necessary Google Cloud resources

set -e

PROJECT_ID="csass-474705"
REGION="us-central1"
SERVICE_NAME="csass-app"
SQL_INSTANCE="csass-474705"
BUCKET_NAME="csass-474705-media"

echo "========================================="
echo "CSASS Cloud Run Deployment Setup"
echo "========================================="
echo ""

# Set the project
echo "Setting project to: $PROJECT_ID"
gcloud config set project $PROJECT_ID

# Enable required APIs
echo ""
echo "Enabling required APIs..."
gcloud services enable run.googleapis.com
gcloud services enable sqladmin.googleapis.com
gcloud services enable cloudbuild.googleapis.com
gcloud services enable secretmanager.googleapis.com
gcloud services enable storage.googleapis.com
gcloud services enable containerregistry.googleapis.com

# Store secrets in Secret Manager
echo ""
echo "Creating secrets in Secret Manager..."

# DB_PASSWORD
echo -n "KQTT5oL7e\$ujK5cc" | gcloud secrets create DB_PASSWORD --data-file=- --replication-policy="automatic" 2>/dev/null || \
echo -n "KQTT5oL7e\$ujK5cc" | gcloud secrets versions add DB_PASSWORD --data-file=-

# SECRET_KEY
echo -n "@(ggq*4*-!r=so-c=7mguzii1#hwd\$26+zb!girkmvkz4_h^)&" | gcloud secrets create SECRET_KEY --data-file=- --replication-policy="automatic" 2>/dev/null || \
echo -n "@(ggq*4*-!r=so-c=7mguzii1#hwd\$26+zb!girkmvkz4_h^)&" | gcloud secrets versions add SECRET_KEY --data-file=-

# SENDGRID_API_KEY
echo -n "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" | gcloud secrets create SENDGRID_API_KEY --data-file=- --replication-policy="automatic" 2>/dev/null || \
echo -n "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" | gcloud secrets versions add SENDGRID_API_KEY --data-file=-

# Get the project number
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format="value(projectNumber)")

# Grant Cloud Run service account access to secrets
echo ""
echo "Granting secret access to Cloud Run service account..."
for SECRET in DB_PASSWORD SECRET_KEY SENDGRID_API_KEY; do
    gcloud secrets add-iam-policy-binding $SECRET \
        --member="serviceAccount:$PROJECT_NUMBER-compute@developer.gserviceaccount.com" \
        --role="roles/secretmanager.secretAccessor"
done

# Create Cloud Storage bucket for media files
echo ""
echo "Creating Cloud Storage bucket: $BUCKET_NAME"
gsutil mb -l $REGION gs://$BUCKET_NAME 2>/dev/null || echo "Bucket already exists"

# Make bucket publicly readable
echo "Making bucket publicly readable..."
gsutil iam ch allUsers:objectViewer gs://$BUCKET_NAME

# Grant Cloud Run service account access to bucket
echo "Granting Cloud Run access to bucket..."
gsutil iam ch serviceAccount:$PROJECT_NUMBER-compute@developer.gserviceaccount.com:objectAdmin gs://$BUCKET_NAME

# Check if Cloud SQL instance exists
echo ""
echo "Checking Cloud SQL instance status..."
if gcloud sql instances describe $SQL_INSTANCE --format="value(name)" 2>/dev/null; then
    echo "Cloud SQL instance '$SQL_INSTANCE' already exists"
else
    echo "ERROR: Cloud SQL instance '$SQL_INSTANCE' not found!"
    echo "Please create it manually or run the following command:"
    echo ""
    echo "gcloud sql instances create $SQL_INSTANCE \\"
    echo "    --database-version=POSTGRES_14 \\"
    echo "    --tier=db-f1-micro \\"
    echo "    --region=$REGION"
    echo ""
    exit 1
fi

# Get Cloud SQL connection name
CONNECTION_NAME=$(gcloud sql instances describe $SQL_INSTANCE --format="value(connectionName)")
echo "Cloud SQL Connection Name: $CONNECTION_NAME"

echo ""
echo "========================================="
echo "Setup Complete!"
echo "========================================="
echo ""
echo "Next Steps:"
echo "1. Push your code to GitHub"
echo "2. Connect GitHub to Cloud Build:"
echo "   - Go to: https://console.cloud.google.com/cloud-build/triggers"
echo "   - Click 'Connect Repository'"
echo "   - Select GitHub and authenticate"
echo "   - Choose your repository"
echo "   - Create a trigger with these settings:"
echo "     * Event: Push to branch"
echo "     * Branch: ^main\$"
echo "     * Configuration: Cloud Build configuration file"
echo "     * Location: /cloudbuild.yaml"
echo ""
echo "3. After first deployment, run migrations:"
echo "   gcloud run services proxy $SERVICE_NAME --region=$REGION &"
echo "   sleep 5"
echo "   python manage.py migrate"
echo ""
echo "4. Create a superuser:"
echo "   python manage.py createsuperuser"
echo ""
echo "Your app will be available at:"
echo "https://$SERVICE_NAME-XXXXX-uc.a.run.app"
echo ""