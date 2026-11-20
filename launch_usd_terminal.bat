@echo off
cd /d "C:\Users\VISHEK\.gemini\antigravity\scratch\global-3asset-trader"
git pull origin main >nul 2>&1
start "" http://localhost:8502
py -m streamlit run dashboard.py --server.port 8502 --server.headless true
