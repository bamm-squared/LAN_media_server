# home_media_server
Intended for use with a Raspberry Pi 4/5

## Modify config.yaml
<code>library_root</code> is where you have the videos stored. Use folders to make navigation easier. 

<code>secret_key/admin_key</code> are for FastAPI and not necessary in this application.

If you want to use a parental pin, set the <code>parental_pin</code> here, and then under adult_paths</code>, list the relative subdirectories you do not want kids to get into. This will prompt the user for a pin on the Roku before allowing access to the folder.

Artwork for the video cards are stored in <code>/library_root/images/</code>. Choose whatever image you want for a video and save it with an identical name to the video. For example, a video <code>Josh_birthday.mp4</code> would pull the card image from <code>/library_root/images/Josh_birthday.png</code>. Works with png, jpg, webp and a few others.

Roku is picky about video format. H.264/AVC has broad support. H.265/HEVC, VP9, and AV1 are supported on most 4K models. Containers are MP4, M4V, MKV, MOV.

## Prep the Pi
<code>
sudo apt update
sudo apt install -y python3-venv python3-pip ufw
\# (optional) create a dedicated user to run the service
sudo adduser --system --group --home /opt/media-server media-server</code>

## Put the app in place
<code>
sudo mkdir -p /opt/media-server
sudo chown -R $USER:$USER /opt/media-server
cd /opt/media-server</code>

##### 
Copy the four files (server.py, requirements.txt, config.yaml, media-server.service) into /opt/media-server/

Create a Python virtualenv (avoids the PEP 668 “externally-managed-environment” issue):
<code>
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt</code>

#####
Quick test (Ctrl+C to stop when satisfied):
<code>
.venv/bin/python server.py</code>

## Install the systemd service
Open the service file and point ExecStart and WorkingDirectory at your folder.

Install & start it:
<code>
sudo cp /opt/media-server/media-server.service /etc/systemd/system/media-server.service
sudo systemctl daemon-reload
sudo systemctl enable --now media-server
sudo systemctl status media-server --no-pager
\# logs
journalctl -u media-server -f
</code>

## Firewall rules with ufw
Allow only private RFC1918 subnets to reach your port; deny the rest.
<code>
sudo ufw default deny incoming
sudo ufw default allow outgoing
\# allow from your home LAN(s) only (pick the one(s) you actually use)
sudo ufw allow from 192.168.0.0/16 to any port 8008 proto tcp
sudo ufw allow from 10.0.0.0/8 to any port 8008 proto tcp
sudo ufw allow from 172.16.0.0/12 to any port 8008 proto tcp
\# (if your server uses 8008 instead, change the port in the commands above)
sudo ufw enable
sudo ufw status verbose
</code>

## Website functionality
<code>
sudo mkdir /opt/media-server/web/
</code>
Paste the code in /web/index.html onto the server at /opt/media-server/web/index.html
