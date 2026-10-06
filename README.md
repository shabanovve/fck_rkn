# fck_rkn

`fck_rkn` is a server-side solution designed to help users bypass internet
blocking and censorship imposed by Roskomnadzor (RKN). It is built around
Google Cloud Platform services.

The project aims to provide:

- **MTProxy for Telegram** to proxy Telegram traffic.
- **A separate proxy for web browsers** to access websites through a proxy.
- **Container orchestration with Docker**.
- **Automated firewall rule updates** to help protect the services from web
  crawlers.
- **Contains two part: RU-server and EU-server**
- **RU-server ups Outline VPN server and connects to EU-server via VPN**
- **EU-server ups Outline VPN server, web proxy and MTProxy for Telegram**
- **RU-server allows only connection to web proxy and MTProxy for Telegram only in EU-server** other outgoing connections are blocked by firewall rules.
- **EU-server allows for VPN clients connection to web proxy and MTProxy for Telegram only** other outgoing connections are blocked by firewall rules.
- **EU-server disables any connection to ru-domain and ru-ip**
- **Monitoring of all services is provided by Prometheus and Grafana dashboards**
- Use project https://github.com/C24Be/AS_Network_List.git to block ingress connection from black list ip addresses in RU-server and EU-server
- Update black list ip addresses in RU-server and EU-server daily from project
- Create stand in virtual machine for autotest functionality of RU-server and EU-server
- All parameters are provided as ENV variables for docker 


Because the proxy services operate at the application layer, this architecture
is intended to prevent the Max messenger from detecting the circumvention
tools.
