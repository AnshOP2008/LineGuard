"""MQTT transport for LineGuard.

Publish a SKAB run as if it were a live pump (one JSON reading per second):
    python -m service.mqtt_bridge publish --run valve1/3 --speed 10

Consume readings, run the online detector and print/log alerts:
    python -m service.mqtt_bridge monitor

Broker defaults to localhost:1883 (docker compose starts Eclipse Mosquitto).
Topic: lineguard/p101/readings
"""

from __future__ import annotations

import argparse
import json
import os
import time

TOPIC = "lineguard/p101/readings"


def _client():
    import paho.mqtt.client as mqtt

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.connect(os.environ.get("MQTT_HOST", "localhost"), int(os.environ.get("MQTT_PORT", 1883)))
    return client


def publish(run_name: str, speed: float) -> None:
    from lineguard.config import SIGNALS
    from lineguard.data import load_runs

    run = next(r for r in load_runs() if r.name == run_name)
    client = _client()
    client.loop_start()
    for _, row in run.frame.iterrows():
        client.publish(TOPIC, json.dumps({"ts": str(row["datetime"]), **{s: float(row[s]) for s in SIGNALS}}))
        time.sleep(1 / speed)
    client.loop_stop()


def monitor() -> None:
    from service.app import limit_multipliers, relations, shipped_config
    from lineguard.online import OnlineMonitor

    mon = OnlineMonitor(shipped_config(), relations(), limit_multipliers())

    def on_message(_client, _userdata, msg):
        event = mon.push(json.loads(msg.payload))
        if "alert" in event:
            a = event["alert"]
            print(f"[{event['t']:>5}s] {a['verdict'].upper():<12} {a['title']}  ->  {a['action']}", flush=True)

    client = _client()
    client.subscribe(TOPIC)
    client.on_message = on_message
    print(f"Listening on {TOPIC}", flush=True)
    client.loop_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["publish", "monitor"])
    parser.add_argument("--run", default="valve1/3")
    parser.add_argument("--speed", type=float, default=10)
    args = parser.parse_args()
    publish(args.run, args.speed) if args.mode == "publish" else monitor()
