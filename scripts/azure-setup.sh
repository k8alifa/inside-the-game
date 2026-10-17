#!/usr/bin/env bash
# Create the Foundry resource, project and model deployment for Inside the Game.
# Run in Azure Cloud Shell (Bash), where you are already signed in.
# Commands follow Microsoft's "create Foundry resources" quickstart (Azure CLI 2.80+).
set -euo pipefail

LOCATION="${LOCATION:-eastus}"
RG="${RG:-itg-rg}"
SUFFIX="${SUFFIX:-$RANDOM}"
RESOURCE="itg-foundry-$SUFFIX"   # also the custom domain, must be globally unique
PROJECT="inside-the-game"
MODEL="${MODEL:-gpt-5-mini}"
MODEL_VERSION="${MODEL_VERSION:-2025-08-07}"

az group create --name "$RG" --location "$LOCATION" -o none

az cognitiveservices account create \
  --name "$RESOURCE" --resource-group "$RG" --kind AIServices --sku S0 \
  --location "$LOCATION" --custom-domain "$RESOURCE" --assign-identity \
  --allow-project-management true -o none

az cognitiveservices account project create \
  --name "$RESOURCE" --resource-group "$RG" --project-name "$PROJECT" \
  --location "$LOCATION" -o none

az cognitiveservices account deployment create \
  --name "$RESOURCE" --resource-group "$RG" \
  --deployment-name "$MODEL" --model-name "$MODEL" --model-version "$MODEL_VERSION" \
  --model-format OpenAI --sku-capacity 10 --sku-name GlobalStandard -o none

# Let your own account call the project from code (Foundry User role, by GUID).
ME=$(az ad signed-in-user show --query id -o tsv)
SCOPE=$(az cognitiveservices account show --name "$RESOURCE" --resource-group "$RG" --query id -o tsv)
az role assignment create --assignee "$ME" --role 53ca6127-db72-4b80-b1b0-d745d6d5456d --scope "$SCOPE" -o none

ENDPOINT=$(az cognitiveservices account project show \
  --name "$RESOURCE" --resource-group "$RG" --project-name "$PROJECT" \
  --query 'properties.endpoints."AI Foundry API"' -o tsv)

echo
echo "Done. Send these two lines back to Claude:"
echo "FOUNDRY_PROJECT_ENDPOINT=$ENDPOINT"
echo "FOUNDRY_MODEL=$MODEL"
