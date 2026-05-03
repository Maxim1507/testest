#!/data/data/com.termux/files/usr/bin/bash
# Einmalige Installation für Termux auf Android
# Ausführen mit: bash termux_setup.sh

set -e

echo "=== Jumbo Checker – Termux Setup ==="

# Paketquellen aktualisieren
pkg update -y

# Python installieren
pkg install python -y

# pip-Pakete
pip install --upgrade pip
pip install requests beautifulsoup4

echo ""
echo "✓ Installation abgeschlossen."
echo ""
echo "Starten mit:"
echo "  python check_jumbo.py"
