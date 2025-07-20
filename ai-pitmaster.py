#!/usr/bin/env python3
import json
import sys
import os
import threading
import queue
from datetime import datetime
from collections import deque
import anthropic
import requests
import subprocess

PITMASTER_WISDOM = """
Key BBQ knowledge:
- Target pit temp: 225-235°F for low and slow
- Brisket done at 195-205°F internal (probe slides in like butter)
- The stall hits around 150-170°F, can last 5+ hours
- The stall can be shortened by increasing cook temperature but it's a balancing act- too hot and it risks making the meat dry and tough, it can be done up to 325F for pork shoulder but brisket is riskier and you should at most take temps up to 275F if you have to for timing purposes
- Texas Crutch (wrapping in foil or paper at 150°F) powers through stall by trapping moisture and preventing evaporation, however that can soften the bark
- Inject with beef broth for moisture (1oz per pound)
- Salt 12-24hrs ahead, or 2-4hr minimum
- Trim fat cap to 1/4", remove silverskin
- Brisket can take ~1.5hr/lb at 225°F, ~1.2hr/lb at 250F, but varies per cook.
- Smoking meat has three stages:
   Stage I (pre-stall): logistic growth model
   Stage II (stall): linear model
   Stage III (post-stall): logistic growth model
   The stall is defined as when |α(t)| ≤ 0.03 where α(t) = f'(t)/f(t)
   The stall typically occurs around 150-170°F internal temp
   An example 10.7lb brisket took 11.35 hours total
   The stages were roughly: Stage I (0-6.6hrs), Stage II (6.6-10hrs), Stage III (10-11.35hrs)
- Let rest in faux cambro (cooler) for 1-4hrs (better yet real cambro)
- Slice against grain at last minute (meat dries fast)
"""

class ClaudeBBQConversation:
    def __init__(self, api_key, target_pit=225, target_meat=203, meat_type="brisket", weight=12, phone=None):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.target_pit = target_pit
        self.target_meat = target_meat
        self.meat_type = meat_type
        self.weight = weight
        self.phone = phone

        # conversation state
        self.messages = []
        self.data_queue = queue.Queue()
        self.temp_history = deque(maxlen=120)  # 1hr at 30s intervals
        self.start_time = datetime.now()
        self.last_update = None
        self.ambient_temp = None

        # track alert states to prevent spam
        self.alert_states = {
            'pit_crash': False,
            'pit_spike': False,
            'stall_approaching': False
        }
        self.last_sms_time = {}
        self.sms_cooldown = 900  # 15min between same alert type

        # init conversation
        init_msg = f"""You're helping me smoke a {weight}lb {meat_type}. Target pit: {target_pit}°F, target meat: {target_meat}°F.

{PITMASTER_WISDOM}

I'll send you temp updates and tell you what I'm doing. Give brief, specific advice. Be casual.

Starting the cook now."""

        self.messages.append({"role": "user", "content": init_msg})
        response = self._ask_claude()
        print(f"\n🤖 {response}\n")

    def send_sms(self, message, alert_type="general"):
        """send sms via txtbelt if not in cooldown"""
        if not self.phone:
            return

        # rate limit by alert type
        if alert_type in self.last_sms_time:
            elapsed = (datetime.now() - self.last_sms_time[alert_type]).seconds
            if elapsed < self.sms_cooldown:
                return

        try:
            resp = requests.post('https://textbelt.com/text', {
                'phone': self.phone,
                'message': f"BBQ: {message}",
                'key': os.environ.get('TXTBELT_KEY', 'textbelt')
            })

            if resp.json().get('success'):
                self.last_sms_time[alert_type] = datetime.now()
                print(f"\n📱 SMS sent: {message}")
            else:
                print(f"\n📱 SMS failed: {resp.json()}")

        except Exception as e:
            print(f"\n📱 SMS error: {e}")

    def check_critical_conditions(self, data):
        pit = data['pit']
        meat = data['meat']

        # pit emergencies
        if pit < self.target_pit - 75:
            if not self.alert_states['pit_crash']:
                self.alert_states['pit_crash'] = True
                self.send_sms(f"pit crashed to {pit:.0f}°F - add fuel NOW", "pit_crash")
                self.handle_user_input("pit temp crashed bad, what to do?")
        else:
            self.alert_states['pit_crash'] = False

        if pit > self.target_pit + 50:
            if not self.alert_states['pit_spike']:
                self.alert_states['pit_spike'] = True
                self.send_sms(f"pit spiked to {pit:.0f}°F - close vents", "pit_spike")
        else:
            self.alert_states['pit_spike'] = False

        # meat milestones
        if 148 < meat < 152 and len(self.temp_history) > 10:
            # approaching stall
            recent_meat = [d['meat'] for d in list(self.temp_history)[-10:]]
            if max(recent_meat) - min(recent_meat) < 3:
                if not self.alert_states['stall_approaching']:
                    self.alert_states['stall_approaching'] = True
                    self.send_sms(f"stall incoming at {meat:.0f}°F - wrap now?", "stall")
        else:
            self.alert_states['stall_approaching'] = False

        # one-time milestones don't need state tracking
        if 195 < meat < 200:
            self.send_sms(f"almost done! meat at {meat:.0f}°F", "done_soon")

        elif meat >= self.target_meat:
            self.send_sms(f"DONE - meat hit {meat:.0f}°F", "done")

    def _ask_claude(self, user_msg=None):
        """send current convo to claude"""
        if user_msg:
            self.messages.append({"role": "user", "content": user_msg})

        try:
            response = self.client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=300,
                temperature=0.5,
                messages=self.messages
            )

            content = response.content[0].text
            self.messages.append({"role": "assistant", "content": content})
            return content

        except Exception as e:
            return f"claude broke: {e}"

    def process_temp_update(self, data):
        """handle new temp data"""
        self.temp_history.append(data)
        self.last_update = data['time']

        # status line
        cook_time = (datetime.now() - self.start_time).total_seconds() / 3600
        status = f"\r[{data['time'].strftime('%H:%M')}] pit:{data['pit']:.0f}°F meat:{data['meat']:.0f}°F"

        if self.ambient_temp:
            status += f" outside:{self.ambient_temp:.0f}°F"

        status += f" | {cook_time:.1f}hrs"
        print(status)  # removed \r and end='', just print normally

        # check for oh shit moments
        self.check_critical_conditions(data)

    def detect_stall_mathematical(self):
        """fancy stall detection"""
        if len(self.temp_history) < 10:
            return False

        # need at least 5min of data
        recent = list(self.temp_history)[-10:]
        times = [(d['time'] - recent[0]['time']).total_seconds() / 60 for d in recent]
        temps = [d['meat'] for d in recent]

        # finite diff for derivative
        if len(temps) < 3:
            return False

        # smoothed derivative using 3-point formula
        dt = times[1] - times[0] if times[1] != times[0] else 1
        f_prime = (temps[-1] - temps[-3]) / (2 * dt)

        # exponential growth rate α(t) = f'(t)/f(t)
        if temps[-2] > 0:  # sanity check
            alpha = f_prime / temps[-2]

            # stall when |α(t)| ≤ 0.03 per the paper
            if abs(alpha) <= 0.03 and 150 < temps[-2] < 170:
                return True

        return False

    def get_temp_summary(self):
        """summarize recent temps for claude"""
        if len(self.temp_history) < 2:
            return "no temp data yet"

        recent = list(self.temp_history)[-20:]  # last 10min
        pit_temps = [d['pit'] for d in recent]
        meat_temps = [d['meat'] for d in recent]

        pit_now = pit_temps[-1]
        meat_now = meat_temps[-1]
        pit_trend = pit_temps[-1] - pit_temps[0]
        meat_rate = (meat_temps[-1] - meat_temps[0]) / (len(meat_temps) / 2) if len(meat_temps) > 1 else 0

        ambient_str = f"{self.ambient_temp:.0f}°F" if self.ambient_temp else "Unknown"

        return f"Temps: pit {pit_now:.0f}°F ({pit_trend:+.1f}/10min), meat {meat_now:.0f}°F ({meat_rate:+.1f}°F/hr), ambient: {ambient_str}"

    def handle_user_input(self, user_input):
        """process what user types"""
        # add temp context
        msg = f"{user_input}\n\nCurrent: {self.get_temp_summary()}"

        print()  # newline after status
        response = self._ask_claude(msg)
        print(f"\n🤖 {response}\n")

    def temp_reader_thread(self):
        """background thread running rtl_433"""
        try:
            # grab both thermopro and lacrosse
            cmd = "rtl_433 -F json"

            print(f"starting rtl_433...")
            proc = subprocess.Popen(
                cmd.split(),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                universal_newlines=True
            )

            for line in proc.stdout:
                try:
                    data = json.loads(line.strip())

                    # bbq thermometer
                    if data.get('model') == 'Thermopro-TP12':
                        parsed = {
                            'time': datetime.strptime(data['time'], '%Y-%m-%d %H:%M:%S'),
                            'pit': data['temperature_1_C'] * 9/5 + 32,  # C to F
                            'meat': data['temperature_2_C'] * 9/5 + 32
                        }
                        self.data_queue.put(parsed)

                    # neighbor's weather station
                    elif data.get('model') == 'LaCrosse-TX141Bv3':
                        self.ambient_temp = data['temperature_C'] * 9/5 + 32

                except:
                    pass

        except Exception as e:
            print(f"rtl_433 died: {e}")

    def run(self):
        """main loop handling user input and temp updates"""
        # start temp reader thread
        temp_thread = threading.Thread(target=self.temp_reader_thread, daemon=True)
        temp_thread.start()

        print("type stuff to tell claude (or 'quit' to exit)")
        print("examples: 'just added 10 briquettes', 'wrapped it', 'windy af today'")
        print("-" * 50)

        while True:
            # check for temp updates
            while not self.data_queue.empty():
                self.process_temp_update(self.data_queue.get())

            # simple user input since stdin is free now
            try:
                import select
                if sys.stdin in select.select([sys.stdin], [], [], 0.1)[0]:
                    user_input = input().strip()

                    if user_input.lower() == 'quit':
                        break
                    elif user_input:
                        self.handle_user_input(user_input)
            except:
                pass

            # periodic check-ins
            if self.last_update:
                elapsed = (datetime.now() - self.last_update).seconds
                if elapsed > 300:  # 5min no data
                    print("\n⚠️ no temp data for 5min - check your sensor")

def main():
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        print("set ANTHROPIC_API_KEY env var")
        sys.exit(1)

    # setup
    print("=== AI pitmaster ===")
    meat_type = input("meat type (default: brisket): ").strip() or "brisket"
    weight = float(input("weight in lbs (default: 12): ").strip() or "12")
    target_pit = int(input("target pit (default: 225): ").strip() or "225")
    target_meat = int(input("target meat (default: 203): ").strip() or "203")

    print("\nSMS alerts?")
    phone = os.environ.get('BBQ_PHONE')
    if not phone:
        phone = input("phone # for alerts (or skip): ").strip()
    else:
        print(f"using phone from BBQ_PHONE env: {phone}")

    if phone and not phone.startswith('+'):
        phone = '+1' + phone  # assume US

    print(f"\nstarting {weight}lb {meat_type} cook...")
    print("rtl_433 will start automatically")

    convo = ClaudeBBQConversation(
        api_key=api_key,
        target_pit=target_pit,
        target_meat=target_meat,
        meat_type=meat_type,
        weight=weight,
        phone=phone
    )

    try:
        convo.run()
    except KeyboardInterrupt:
        print("\n\ncook terminated. bon appetit")

if __name__ == "__main__":
    main()
