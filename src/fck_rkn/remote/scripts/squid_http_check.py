import http.client
import sys

connection = http.client.HTTPConnection("127.0.0.1", int(sys.argv[1]), timeout=30)
connection.request("GET", "http://youtube.com/", headers={"Host": "youtube.com"})
response = connection.getresponse()
status = response.status
response.close()
connection.close()
print(status)
sys.exit(not 200 <= status < 400)
