# fck_rkn

`fck_rkn` is a server-side solution designed to help users bypass internet
blocking and censorship imposed by Roskomnadzor (RKN). It is built around
Google Cloud Platform services.

The project aims to provide:

- **MTProxy for Telegram** to proxy Telegram traffic.
- **A separate proxy for web browsers** to access websites through a proxy.
- **Container orchestration with Docker**.
- **Two servers: RU-server and EU-server.**
- **RU-server** runs an Outline VPN server and connects to EU-server via VPN.
- **EU-server** runs an Outline VPN server, a web proxy and MTProxy for
  Telegram.
- **Firewall on RU-server:** incoming SSH and VPN are allowed; connections
  from RKN crawlers and all other incoming connections are logged and denied.
  Outgoing connections are limited to those needed for normal VM operation
  (DNS, DHCP and similar) and by the project (such as the VPN to EU-server);
  everything else is logged and denied.
- **VPN clients** can use only the web proxy and MTProxy. They cannot send
  packets beyond RU-server; for example, they cannot ping external services.
- **Firewall on EU-server:** incoming SSH is allowed from any address, VPN
  only from RU-server; connections from RKN crawlers and all other incoming
  connections are logged and denied. Outgoing connections are not restricted.
- **Both servers are hidden from RKN crawlers.** The
  https://github.com/C24Be/AS_Network_List.git project provides the list of
  crawler addresses; the list is refreshed daily on both servers.
- **Blocking of ru-domains** is done at the Squid (web proxy) level. The goal
  of the architecture is that RKN never learns the address of EU-server or
  anything else about it. Russian services (such as the Max messenger, Gosuslugi,
  RuStore, Mir Pay, Sber, VTB, Tinkoff, Yandex services, VK Messenger,
  Odnoklassniki, Wildberries, Ozon, Avito, X5, Megamarket, Samokat, MTS and
  2GIS) can detect circumvention tools and report to RKN, so traffic to them
  must not go through the circumvention path and reveal the EU-server address.
- **Automated firewall rule updates** to help protect the services from web
  crawlers.
- **Monitoring of all services** is provided by Prometheus and Grafana
  dashboards.
- **A test stand** in virtual machines for autotests of RU-server and
  EU-server.
- **All parameters** are provided as ENV variables for Docker.

The detailed firewall requirements are in [DEVELOPMENT.md](DEVELOPMENT.md).

Because the proxy services operate at the application layer, this architecture
is intended to prevent the Max messenger from detecting the circumvention
tools.
