# MickeyDogeFunctions
Doge Assessment Project

# ECFR Agencies Azure Functions

This repository contains a Python Azure Functions project that:

1. Loads agency metadata from the ECFR API and writes it to an Azure Data Table  
2. Exposes two HTTP endpoints (`LoadAgencies` and `GetAgencies`)  
3. Runs a daily timer-trigger to recalculate regulation sizes and detect changes  

---

## ▶️ Directory Structure

```
.vscode/
  ├─ extensions.json
  ├─ launch.json
  ├─ settings.json
  └─ tasks.json
.funcignore
.gitignore
function_app.py      # main Functions code
host.json            # Functions host configuration
local.settings.json  # local-only settings (ignored by Git)
requirements.txt     # Python dependencies
README.md            # this file
```

---

## 🛠 Prerequisites

- **Azure Subscription**  
- **Python 3.11 (with `pip`)  
- **Azure CLI** (>= 2.20)  
- **Azure Functions Core Tools** (>= 4.x)  
- **(Optional) VS Code** + Azure Functions extension  

---

## ⚙️ Local Setup

1. **Clone the repo**  
   ```bash
   git clone https://github.com/<your-org>/ecfr-agencies-functions.git
   cd ecfr-agencies-functions
   ```

2. **Install dependencies**  
   ```bash
   python -m venv .venv
   source .venv/bin/activate      # on Windows: .venv\\Scripts\\activate
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

3. **Configure local settings**  
   - Copy `local.settings.json` (already provided)  
   - Ensure `AzureWebJobsStorage` and `TABLE_CONN` point to your **Storage Account** connection string.  

4. **Run locally**  
   ```bash
   func start
   ```
   - HTTP endpoints available at `http://localhost:7071/api/LoadAgencies` and `/api/GetAgencies`.  
   - Timer trigger runs on startup and then every minute (per your CRON).

---

## ☁️ Deploy to Azure

1. **Login & select subscription**  
   ```bash
   az login
   az account set --subscription "<your-subscription-id>"
   ```

2. **Create a resource group**  
   ```bash
   az group create \
     --name ecfr-functions-rg \
     --location eastus
   ```

3. **Provision a Storage Account**  
   ```bash
   az storage account create \
     --name ecfrstorage$(date +%s) \
     --resource-group ecfr-functions-rg \
     --location eastus \
     --sku Standard_LRS
   ```

4. **Create the Azure Table**  
   ```bash
   az extension add --name storage-preview
   az storage table create \
     --name Agencies \
     --account-name <your-storage-account> \
     --account-key <your-account-key>
   ```

5. **Create the Function App**  
   ```bash
   az functionapp create \
     --resource-group ecfr-functions-rg \
     --consumption-plan-location eastus \
     --runtime python \
     --runtime-version 3.8 \
     --functions-version 4 \
     --name ecfr-agencies-func \
     --storage-account <your-storage-account>
   ```

6. **Configure App Settings**  
   ```bash
   az functionapp config appsettings set \
     --name ecfr-agencies-func \
     --resource-group ecfr-functions-rg \
     --settings \
       FUNCTIONS_WORKER_RUNTIME=python \
       TABLE_CONN="<your-TABLE_CONN-connection-string>" \
       AzureWebJobsStorage="<your-AzureWebJobsStorage-connection-string>"
   ```

7. **Deploy your code**  
   ```bash
   func azure functionapp publish ecfr-agencies-func --python
   ```

---

## 🔄 Post-Deployment

- **Load agencies**:  
  ```bash
  curl -X POST https://<your-app>.azurewebsites.net/api/LoadAgencies
  ```
- **Get agencies**:  
  ```bash
  curl https://<your-app>.azurewebsites.net/api/GetAgencies
  ```

- **Monitor and Logs**  
  - Use **Azure Portal** → Function App → Functions → Select a function → Monitor  
  - Or the **Log Stream** in the Function App blade.

---

## ⚙️ CI/CD (Optional)

You can wire this repo into an Azure DevOps or GitHub Actions pipeline using the [Azure/functions-action](https://github.com/Azure/functions-action) to deploy on every push to `main`.

---

## 📝 Notes

- The timer-trigger CRON in `function_app.py` (`0 0 * * * *`) runs at midnight UTC daily.  
- All entities are stored in the **Agencies** table under partition `"AGENCY"`.  
- Adjust `host.json` sampling settings or add Application Insights instrumentation as needed.

---

## 📄 License

MIT © Muneeb Akhter