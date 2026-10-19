if ! command -v docker >/dev/null 2>&1 || ! command -v curl >/dev/null 2>&1; then
  sudo apt-get update
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io curl
fi
sudo systemctl enable --now docker
