# Factory motor temperatures

One publisher reads `config.yaml` and sends two production lines to two brokers. Each line has three motors, and the only measurement is temperature. `line_1` goes to the standalone node's broker. `line_2` goes to the operator node's broker.

| timestamp | site | line | machine | temperature_c |
| --- | --- | --- | --- | --- |
| 2026-10-29 10:00:00 | factory_a | line_1 | motor_7 | 72.4 |
| 2026-10-29 10:00:01 | factory_a | line_1 | motor_7 | 72.8 |

`line_1` publishes `motor_7`, `motor_8`, and `motor_9`. `line_2` publishes `motor_10`, `motor_11`, and `motor_12`.

The same reading is stored in `mydb.sensor_readings` and exposed on a UNS path:

```text
company/factory_a/line_1/motor_7/temperature
```

## Requirements

- Docker, for the AnyLog nodes and Edge Data Manager
- Python 3.10 or newer for the publishers
- An AnyLog license key and Docker Hub access for `anylogco/anylog-network`

The repository virtualenv is shared. From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r MQTT/factory/requirements.txt
```

Publisher commands below run from the repository root. The config file next to `publisher.py` is the default.

## AnyLog nodes

This uses the [AnyLog docker-compose](https://github.com/AnyLog-co/docker-compose) layouts. The standalone node is a master and an operator on one agent. The second node is an operator in its own cluster, so each line's rows stay on the node that ingested them. The standalone node also runs the query service, so one SQL statement returns both lines.

Clone the deployment repo and log in. AnyLog images are private; request the Docker password and license from [anylog.network/download](https://www.anylog.network/download).

```bash
git clone https://github.com/AnyLog-co/docker-compose
cd docker-compose
docker login -u anyloguser -p [Docker Login Passkey]
```

The checked-in configs already use different ports:

| Node | TCP | REST | MQTT broker | Cluster |
| --- | --- | --- | --- | --- |
| standalone-operator | 32148 | 32149 | 32150 | anylog-cluster1 |
| operator | 32158 | 32159 | 32160 | anylog-cluster2 |

On the standalone config, `docker-makefiles/anylog-standalone-operator/node_configs.env`, set:

```dotenv
LICENSE_KEY="your-license-key"
ENABLE_OPCUA=false
ENABLE_MQTT=false
```

On the operator config, `docker-makefiles/anylog-operator/node_configs.env`, set:

```dotenv
LICENSE_KEY="your-license-key"
ENABLE_MQTT=false
```

`ENABLE_MQTT=false` keeps the nodes from subscribing to the sample public broker. The commands later in this file are the only subscriptions. `LEDGER_CONN` on the operator is already `127.0.0.1:32148`, which is the standalone TCP port. Leave the two `CLUSTER_NAME` values different (`anylog-cluster1` and `anylog-cluster2`) so the nodes do not form a high-availability pair.

On Docker Desktop (macOS or Windows), host networking is not the Linux default. In Docker Desktop, enable host networking, then set `OVERLAY_IP` in both `node_configs.env` files to this machine's LAN address:

```bash
ifconfig en0 | grep "inet "
```

```dotenv
OVERLAY_IP=192.168.1.20
```

Start the standalone node first, then the operator:

```bash
make up ANYLOG_TYPE=standalone-operator
make up ANYLOG_TYPE=operator
```

Confirm both nodes:

```bash
make full-test ANYLOG_TYPE=standalone-operator
make full-test ANYLOG_TYPE=operator
```

`full-test` checks that the process is up, the node configuration is valid, and the node can see its peer.

## Edge Data Manager

The GUI is the Remote-GUI service. From `docker-compose/support`:

```bash
cd support
make up SERVICE=remote-gui
```

Open [http://127.0.0.1:31800](http://127.0.0.1:31800). The API listens on port `8080`. Add the standalone node as `127.0.0.1:32149` and the operator as `127.0.0.1:32159`.

On macOS, `support/remote-gui/configs.yaml` sets `REMOTE_GUI_NIC` to `eth0`. If that interface does not exist, set it to `en0` and run `make up SERVICE=remote-gui` again.

## Publish

`config.yaml` is the whole deployment. `brokers` names each AnyLog broker and the line it receives. `lines` names that line's three motors. `publish` sets the UNS prefix, the site, and the rate in hertz.

```yaml
brokers:
  - host: 127.0.0.1
    port: 32150
    line: line_1
  - host: 127.0.0.1
    port: 32160
    line: line_2
```

`32150` is the standalone node's broker. `32160` is the operator node's broker. To put a line on another machine, change that broker's `host`.

```bash
.venv/bin/python "MQTT/factory/publisher.py" --dry-run
.venv/bin/python "MQTT/factory/publisher.py"
```

`--dry-run` prints one reading per motor and does not connect. `--once` sends one round and exits. `--hertz 5` overrides `publish.hertz`.

Each broker gets one MQTT connection. That connection publishes the line's three motors. A reading is one JSON object:

```json
{
  "timestamp": "2026-10-29 10:00:00",
  "site": "factory_a",
  "line": "line_1",
  "machine": "motor_7",
  "temperature_c": 72.4
}
```

Topics:

```text
company/factory_a/line_1/motor_7/temperature
company/factory_a/line_1/motor_8/temperature
company/factory_a/line_1/motor_9/temperature
company/factory_a/line_2/motor_10/temperature
company/factory_a/line_2/motor_11/temperature
company/factory_a/line_2/motor_12/temperature
```

To watch a node broker from another terminal:

```bash
mosquitto_sub -h 127.0.0.1 -p 32150 -t 'company/factory_a/line_1/#' -v
mosquitto_sub -h 127.0.0.1 -p 32160 -t 'company/factory_a/line_2/#' -v
```

## Subscribe from the two nodes

Attach to a node, press Enter twice to reach the prompt, and paste that node's command. Detach with `ctrl-d`.

```bash
make attach ANYLOG_TYPE=standalone-operator
make attach ANYLOG_TYPE=operator
```

`dynamic=true` builds `sensor_readings` from the JSON keys and writes a UNS policy for each topic. `table=sensor_readings` keeps every motor in that one table. `master_node` is the standalone TCP address, which is where those policies are stored.

If you change the payload or the topic filter, stop the clients and drop the table before subscribing again:

```text
exit msg client all
drop table sensor_readings where dbms = mydb
```

`broker=local` is that node's own MQTT broker, the same port the publisher is writing to.

On the standalone node, take `line_1`:

```text
<run msg client where
  broker=local and
  user-agent=anylog and
  master_node=127.0.0.1:32148 and
  log=false and
  topic=(
    name=company/factory_a/line_1/# and
    dbms="mydb" and
    table="sensor_readings" and
    dynamic=true
  )>

run uns streamer
```

On the operator node, take `line_2`:

```text
<run msg client where
  broker=local and
  user-agent=anylog and
  master_node=127.0.0.1:32148 and
  log=false and
  topic=(
    name=company/factory_a/line_2/# and
    dbms="mydb" and
    table="sensor_readings" and
    dynamic=true
  )>

run uns streamer
```

AnyLog topic filters accept an exact name or a trailing `#`. Do not use `+` in these commands.

Check that both nodes own the table. Run this on the standalone node:

```text
get data nodes
get columns where dbms = mydb and table = sensor_readings
get streaming
```

## Query one motor

Run this on the standalone node. It has the query service, so the result includes rows stored on the operator as well as rows stored locally.

```text
run client () sql mydb format=table "SELECT timestamp, temperature_c FROM sensor_readings WHERE site = 'factory_a' AND line = 'line_1' AND machine = 'motor_7' ORDER BY timestamp DESC"
```

That is the same statement an application sends:

```sql
SELECT timestamp, temperature_c
FROM sensor_readings
WHERE site = 'factory_a'
  AND line = 'line_1'
  AND machine = 'motor_7'
ORDER BY timestamp DESC;
```

The MQTT topic path for that row is `company/factory_a/line_1/motor_7/temperature`. `motor_8` and `motor_9` use the same prefix with their own machine name. `line_2` uses `company/factory_a/line_2/motor_10/temperature` and the matching machine names.

## Manufacturer path

`uns/` is a second namespace. It groups the same motors by manufacturer:

```text
manufacturer/abb/motor_7/temperature
manufacturer/abb/motor_8/temperature
manufacturer/abb/motor_9/temperature
manufacturer/siemens/motor_10/temperature
manufacturer/siemens/motor_11/temperature
manufacturer/siemens/motor_12/temperature
```

`line_1` is ABB. `line_2` is Siemens. `manufacturer`, `abb`, and `siemens` are new objects. `siemens` selects `line = 'line_2'`. `motor_10` points at the existing object `company_factory_a_line_2_motor_10`. Its temperature node points at the existing temperature object for that motor. The script reads those generated ids and stores them on the uns policy.

```bash
python3 MQTT/factory/uns/publish_manufacturer.py 127.0.0.1:32149 MQTT/factory/uns/manufacturer.json
```

## ISA-95 path

`uns/isa95.json` is the equipment hierarchy. Enterprise, site, area, work center, and work unit:

```text
enterprise/factory_a/line_1/motor_7/temperature
enterprise/factory_a/line_2/motor_10/temperature
```

`enterprise` is a new object for `site = 'factory_a'`. `factory_a` and each line point at the objects already created for `company/factory_a` and `company/factory_a/line_1`. Motors and temperatures point at those existing objects, the same ones used by the manufacturer path.

```bash
python3 MQTT/factory/uns/publish_manufacturer.py 127.0.0.1:32149 MQTT/factory/uns/isa95.json
```

Read one leaf back. Its `object_id` is the id of the existing `motor_7` temperature object.

```text
blockchain get uns where namespace=enterprise/factory_a/line_1/motor_7/temperature
```

```text
run client () sql mydb format=table "SELECT timestamp, temperature_c FROM sensor_readings WHERE line = 'line_1' AND machine = 'motor_7' ORDER BY timestamp DESC"
```
