#!/usr/bin/env python3
"""
AI Pitmaster:
– Tracks ThermoPro TP12 temps via rtl_433
– Chats with Claude (Anthropic) for advice
– Sends SMS alerts via TextBelt
– Fits a 5‑parameter logistic curve on Stage I to predict wrap/finish times
"""

import json
import sys
import os
import threading
import queue
import subprocess
import math
import glob
import shutil
from datetime import datetime, timedelta
from collections import deque

import requests
import anthropic

# ----- optional SciPy for curve fitting -------------------------------------
try:
    from scipy.optimize import curve_fit          # needs scipy >= 1.9
except ModuleNotFoundError:
    curve_fit = None
# ----------------------------------------------------------------------------

PITMASTER_WISDOM = """
Key BBQ knowledge:
- Target pit temp: 225-235°F for low and slow. Can go up to 275°F for quicker cooks (e.g., pulled pork for timing), but risk of dryness increases. Turkey often done at 275-325°F for crispy skin.
- Brisket done at 195-205°F internal (probe slides in like butter). Slice against the grain.
- Pork Shoulder/Butt (for pulled pork) done at 195-205°F (sometimes up to 210°F), often pulled when probe goes in like butter or "jiggles like jello". Let rest 1-2 hours before pulling.
- Pork Ribs done when they bend easily and meat starts to pull back from bones (bend test), or internal temp of 195-205°F. Memphis style (dry rub), KC style (sauce on finish).
- Turkey done at 165°F breast temp (USDA safe), but many prefer 170-175°F breast for better texture. Dark meat safe at 165°F but better at 175-180°F. Turkey doesn't stall like pork/brisket. Spatchcocking recommended for even cooking.
- The stall hits around 150-170°F for pork and brisket, can last 3-5+ hours. It's a plateau where moisture evaporates, cooling the meat. Turkey typically doesn't stall significantly.
- The stall can be shortened by increasing cook temperature but it's a balancing act – too hot and it risks making the meat dry and tough; it can be done up to 325°F for pork shoulder but brisket is riskier and you should at most take temps up to 275°F if you have to for timing purposes.
- Texas Crutch (wrapping in foil or paper) powers through stall by trapping moisture but can soften the bark. Wrap brisket around 150-170°F. Wrap pork shoulder often around 160-170°F. Wrap ribs after 2-3 hours or when bark is set. Turkey generally not wrapped - want crispy skin.
- Inject with beef broth for brisket moisture (≈1 oz per lb). Pork shoulder often injected with apple juice or other liquids. Turkey benefits from butter/herb injection or brining (wet or dry) 12-24 hours ahead.
- Salt 12‑24 h ahead (2‑4 h minimum). For turkey, dry brine with salt/herbs 24h ahead highly recommended for moisture and flavor.
- Brisket: Trim fat cap to 1/4", remove silverskin.
- Pork Shoulder: No need to trim extensively, fat renders.
- Pork Ribs: Remove membrane from bone side for better seasoning penetration and tenderness.
- Turkey: Remove giblets, pat dry thoroughly (especially if brined). Tuck wing tips, tie legs if desired. Can spatchcock for faster/more even cooking.
- Brisket can take ~1.5 h/lb at 225°F, ~1.2 h/lb at 250°F.
- Pork Shoulder can take ~1.5-2 h/lb.
- Pork Ribs can take 5-6 hours using 3-2-1 method (unwrapped 3h, wrapped 2h, sauce 1h) or longer for "low and slow".
- Turkey can take ~30-40 min/lb at 225-250°F (~4-6 hours for 12-14 lb bird), or 15-20 min/lb at 325°F. Spatchcocked turkey cooks faster.
- Smoking meat has three stages:
   Stage I (pre‑stall): Rapid temp rise, logistic growth.
   Stage II (stall): Temperature plateaus, linear or slow rise.
   Stage III (post‑stall): Temperature rises again towards target, logistic growth.
   Stall when |α(t)| ≤ 0.03 (α = f'/f, units h⁻¹) and 150‑170°F internal.
   Note: Turkey typically doesn't exhibit significant stall behavior like pork/brisket.
"""

# ============================ Conversation Class ============================

class ClaudeBBQConversation:
    def __init__(self, api_key, target_pit=225, target_meat=203,
                 meat_type="brisket", weight=12, phone=None,
                 session_file=".bbq_session.json"):

        self.client       = anthropic.Anthropic(api_key=api_key)
        self.target_pit   = target_pit
        self.target_meat  = target_meat
        self.meat_type    = meat_type
        self.weight       = weight
        self.phone        = phone
        self.session_file = session_file

        # conversation & telemetry state
        self.messages      = []
        self.data_queue    = queue.Queue()
        self.temp_history  = deque()  # keep all temp readings for complete cook history
        self.start_time    = datetime.now()
        self.last_update   = None
        self.ambient_temp  = None

        # SMS spam prevention
        self.alert_states  = {'pit_crash': False,
                              'pit_spike': False,
                              'stall_approaching': False}
        self.last_sms_time = {}
        self.sms_cooldown  = int(os.getenv("BBQ_SMS_COOLDOWN", "900"))
        
        # Display and monitoring control
        self.last_display_time = None
        self.display_interval = int(os.getenv("BBQ_DISPLAY_INTERVAL", "120"))  # seconds between temp displays
        self.last_proactive_check = datetime.now()
        self.proactive_check_interval = int(os.getenv("BBQ_PROACTIVE_INTERVAL", "300"))  # seconds between proactive checks

        # Session persistence
        self.last_save_time = None
        self.save_interval = int(os.getenv("BBQ_SAVE_INTERVAL", "60"))  # seconds between auto-saves
        
        # Context tracking for smarter alerts
        self.recent_user_actions = deque(maxlen=10)  # track last 10 user inputs with timestamps
        self.last_fuel_mention = None
        self.temp_recovery_in_progress = False

        # ---------------- new model‑fitting fields ----------------
        self.model_params  = None       # (K, k, λ, D, γ)
        self.eta_wrap      = None
        self.eta_finish    = None
        self.model_rmse    = None
        # ----------------------------------------------------------

        init_msg = f"""You're helping me smoke a {weight} lb {meat_type}.
Target pit: {target_pit} °F. Target meat: {target_meat} °F.

{PITMASTER_WISDOM}

I'll feed you temp updates and notes.  Reply with brief, specific, casual advice.

Starting the cook now."""
        self.messages.append({"role": "user", "content": init_msg})
        print(f"\n🤖 {self._ask_claude()}\n")

    # --------------------------------------------------------------------- #
    #                            Utility methods                            #
    # --------------------------------------------------------------------- #

    def save_session(self):
        """Save current session state to disk."""
        session_data = {
            'metadata': {
                'meat_type': self.meat_type,
                'weight': self.weight,
                'target_pit': self.target_pit,
                'target_meat': self.target_meat,
            },
            'start_time': self.start_time.isoformat(),
            'last_update': self.last_update.isoformat() if self.last_update else None,
            'ambient_temp': self.ambient_temp,
            'messages': self.messages,  # save all messages for complete history
            'temp_history': [
                {
                    'time': d['time'].isoformat(),
                    'pit': d['pit'],
                    'meat': d['meat']
                }
                for d in list(self.temp_history)
            ],
            'alert_states': self.alert_states,
            'last_sms_time': {
                k: v.isoformat() for k, v in self.last_sms_time.items()
            },
            'recent_user_actions': [
                {
                    'time': a['time'].isoformat(),
                    'message': a['message'],
                    'input': a['input']
                }
                for a in list(self.recent_user_actions)
            ],
            'last_fuel_mention': self.last_fuel_mention.isoformat() if self.last_fuel_mention else None,
            'temp_recovery_in_progress': self.temp_recovery_in_progress,
            'last_display_time': self.last_display_time.isoformat() if self.last_display_time else None,
            'last_proactive_check': self.last_proactive_check.isoformat(),
            'model_params': self.model_params,
            'eta_wrap': self.eta_wrap,
            'eta_finish': self.eta_finish,
            'model_rmse': self.model_rmse,
        }

        try:
            with open(self.session_file, 'w') as f:
                json.dump(session_data, f, indent=2)
        except Exception as e:
            print(f"⚠️  Failed to save session: {e}")

    @classmethod
    def load_session(cls, api_key, session_file=".bbq_session.json", phone=None):
        """Load a previous session from disk."""
        try:
            with open(session_file, 'r') as f:
                data = json.load(f)

            # Create instance without calling initial Claude conversation
            instance = cls.__new__(cls)
            instance.client = anthropic.Anthropic(api_key=api_key)
            instance.session_file = session_file
            instance.phone = phone

            # Restore metadata
            metadata = data['metadata']
            instance.meat_type = metadata['meat_type']
            instance.weight = metadata['weight']
            instance.target_pit = metadata['target_pit']
            instance.target_meat = metadata['target_meat']

            # Restore state
            instance.start_time = datetime.fromisoformat(data['start_time'])
            instance.last_update = datetime.fromisoformat(data['last_update']) if data['last_update'] else None
            instance.ambient_temp = data['ambient_temp']
            instance.messages = data['messages']

            # Restore temperature history
            instance.temp_history = deque(
                [
                    {
                        'time': datetime.fromisoformat(d['time']),
                        'pit': d['pit'],
                        'meat': d['meat']
                    }
                    for d in data['temp_history']
                ],
            )

            # Restore alert states
            instance.alert_states = data['alert_states']
            instance.last_sms_time = {
                k: datetime.fromisoformat(v) for k, v in data['last_sms_time'].items()
            }

            # Restore context tracking
            instance.recent_user_actions = deque(
                [
                    {
                        'time': datetime.fromisoformat(a['time']),
                        'message': a['message'],
                        'input': a['input']
                    }
                    for a in data['recent_user_actions']
                ],
                maxlen=10
            )
            instance.last_fuel_mention = datetime.fromisoformat(data['last_fuel_mention']) if data['last_fuel_mention'] else None
            instance.temp_recovery_in_progress = data['temp_recovery_in_progress']
            instance.last_display_time = datetime.fromisoformat(data['last_display_time']) if data['last_display_time'] else None
            instance.last_proactive_check = datetime.fromisoformat(data['last_proactive_check'])

            # Restore model state
            instance.model_params = data['model_params']
            instance.eta_wrap = data['eta_wrap']
            instance.eta_finish = data['eta_finish']
            instance.model_rmse = data['model_rmse']

            # Restore config values from env
            instance.sms_cooldown = int(os.getenv("BBQ_SMS_COOLDOWN", "900"))
            instance.display_interval = int(os.getenv("BBQ_DISPLAY_INTERVAL", "120"))
            instance.proactive_check_interval = int(os.getenv("BBQ_PROACTIVE_INTERVAL", "300"))
            instance.save_interval = int(os.getenv("BBQ_SAVE_INTERVAL", "60"))

            # Non-persistent state
            instance.data_queue = queue.Queue()
            instance.last_save_time = datetime.now()  # reset save timer

            cook_hours = (datetime.now() - instance.start_time).total_seconds() / 3600
            print(f"\n✅ Session restored! Cook time: {cook_hours:.1f}h, {len(instance.temp_history)} temp readings\n")

            return instance

        except FileNotFoundError:
            return None
        except Exception as e:
            print(f"⚠️  Failed to load session: {e}")
            return None

    def send_sms(self, message, alert_type="general"):
        if not self.phone:
            return
        last = self.last_sms_time.get(alert_type)
        if last and (datetime.now() - last).seconds < self.sms_cooldown:
            return  # still in cooldown

        try:
            resp = requests.post('https://textbelt.com/text', {
                'phone': self.phone,
                'message': f"BBQ: {message}",
                'key': os.getenv('TXTBELT_KEY', 'textbelt')
            }).json()
            if resp.get('success'):
                self.last_sms_time[alert_type] = datetime.now()
                print(f"\n📱 SMS sent: {message}")
            else:
                print(f"\n📱 SMS failed: {resp}")
        except Exception as e:
            print(f"\n📱 SMS error: {e}")

    def _estimate_tokens(self, messages):
        """Rough token estimate: ~4 chars per token"""
        total_chars = sum(len(str(m.get('content', ''))) for m in messages)
        return total_chars // 4

    def _summarize_old_messages(self):
        """Summarize old messages if approaching context window limit."""
        estimated_tokens = self._estimate_tokens(self.messages)

        # If over 150K tokens (~75% of 200K), summarize older messages
        if estimated_tokens > 150000 and len(self.messages) > 50:
            initial_msg = self.messages[0]
            messages_to_summarize = self.messages[1:-40]
            recent_messages = self.messages[-40:]

            # Build summary
            summary_content = "Summary of earlier cook session:\n"
            for msg in messages_to_summarize:
                role = msg['role']
                content = msg['content'][:200]
                summary_content += f"[{role}] {content}\n"

            summary_msg = {
                "role": "user",
                "content": f"{summary_content}\n(Earlier messages summarized to save context.)"
            }

            self.messages = [initial_msg, summary_msg] + recent_messages
            print(f"📝 Summarized conversation history (~{estimated_tokens} tokens)")

    def _ask_claude(self, user_msg=None):
        if user_msg:
            self.messages.append({"role": "user", "content": user_msg})

        self._summarize_old_messages()

        try:
            response = self.client.messages.create(
                model="claude-sonnet-4-5-20250929",
                max_tokens=300,
                temperature=0.2,
                messages=self.messages
            )
            content = response.content[0].text
            self.messages.append({"role": "assistant", "content": content})
            return content
        except Exception as e:
            return f"claude broke: {e}"

    # --------------------------------------------------------------------- #
    #                        Temperature & alerts                           #
    # --------------------------------------------------------------------- #

    def check_critical_conditions(self, data):
        pit  = data['pit']
        meat = data['meat']

        if pit < self.target_pit - 75:
            if not self.alert_states['pit_crash']:
                self.alert_states['pit_crash'] = True
                self.send_sms(f"Pit crashed to {pit:.0f}°F – add fuel NOW", "pit_crash")
                self.handle_user_input("pit temp crashed, what to do?")
        else:
            self.alert_states['pit_crash'] = False

        if pit > self.target_pit + 50:
            if not self.alert_states['pit_spike']:
                self.alert_states['pit_spike'] = True
                self.send_sms(f"Pit spiked to {pit:.0f}°F – close vents", "pit_spike")
        else:
            self.alert_states['pit_spike'] = False

        if 148 < meat < 152 and len(self.temp_history) > 10:
            recent_meat = [d['meat'] for d in list(self.temp_history)[-10:]]
            if max(recent_meat) - min(recent_meat) < 3:
                if not self.alert_states['stall_approaching']:
                    self.alert_states['stall_approaching'] = True
                    self.send_sms(f"Stall incoming at {meat:.0f}°F – wrap now?", "stall")
        else:
            self.alert_states['stall_approaching'] = False

        # Check if meat is almost done (between 195-200°F) OR has reached target temp
        if 195 < meat < 200:
            self.send_sms(f"Almost done! Meat at {meat:.0f}°F", "done_soon")
        if meat >= self.target_meat:
            self.send_sms(f"DONE – meat hit {meat:.0f}°F", "done")

    def check_gradual_trends(self, data):
        """Check for concerning gradual trends and speak up proactively with context awareness"""
        if len(self.temp_history) < 20:
            return
            
        recent = list(self.temp_history)[-20:]  # last ~10 minutes of data
        pit_temps = [d['pit'] for d in recent]
        now = datetime.now()
        
        # Check for sustained pit temp decline
        if len(pit_temps) >= 10:
            early_avg = sum(pit_temps[:5]) / 5
            late_avg = sum(pit_temps[-5:]) / 5
            decline = early_avg - late_avg
            
            # Check if temperature is recovering after fuel addition
            if self.temp_recovery_in_progress:
                if decline < 5:  # temp stabilized or improving
                    self.temp_recovery_in_progress = False
                else:
                    # Still declining despite fuel - that's concerning
                    pass
            
            # Only alert if decline is significant AND we haven't recently discussed fuel
            if decline >= 15:
                should_alert = self._should_alert_about_temp_decline(now, decline)
                if should_alert:
                    print(f"\n⚠️  Pit temp trending down: -{decline:.0f}°F over recent readings")
                    self.handle_user_input("pit temp declining steadily, should I add fuel?")

    def _should_alert_about_temp_decline(self, now, decline):
        """Use contextual reasoning to decide if we should alert about temperature decline"""
        
        # Don't alert if we just mentioned fuel (give time for recovery)
        if self.last_fuel_mention:
            minutes_since_fuel = (now - self.last_fuel_mention).total_seconds() / 60
            if minutes_since_fuel < 15:  # Give 15 minutes for fuel to take effect
                return False
        
        # Check recent user messages for fuel-related context
        recent_fuel_discussion = False
        for action in list(self.recent_user_actions)[-5:]:  # last 5 messages
            time_diff = (now - action['time']).total_seconds() / 60
            if time_diff < 20:  # within 20 minutes
                fuel_keywords = ['fuel', 'coal', 'wood', 'charcoal', 'briquette', 'added', 'add', 'fire']
                if any(keyword in action['message'] for keyword in fuel_keywords):
                    recent_fuel_discussion = True
                    break
        
        # Don't alert if we recently discussed fuel unless it's been a while
        if recent_fuel_discussion:
            return decline >= 25  # Only alert for severe declines if we recently discussed fuel
        
        # If temperature is actively recovering, don't alert
        if self.temp_recovery_in_progress:
            return decline >= 20  # Higher threshold during recovery
        
        # Default case - alert for significant declines
        return decline >= 15

    # ---------------------------- Stall detector --------------------------

    def detect_stall_mathematical(self):
        """Return True if Henderson stall criterion is met."""
        if len(self.temp_history) < 10:
            return False

        recent = list(self.temp_history)[-10:]
        times_s = [(d['time'] - recent[0]['time']).total_seconds() for d in recent]
        temps_f = [d['meat'] for d in recent]

        if len(set(times_s)) < 3:
            return False  # timestamps not distinct

        # centred 3‑point finite diff on last 3 samples
        t1, t0, tm1 = times_s[-1], times_s[-2], times_s[-3]
        f1, f0, fm1 = temps_f[-1], temps_f[-2], temps_f[-3]

        dt_hours = (t1 - tm1) / 3600.0
        if dt_hours == 0:
            return False

        f_prime = (f1 - fm1) / (2 * dt_hours)  # °F h⁻¹
        alpha   = f_prime / f0                 # h⁻¹

        return 150 <= f0 <= 170 and abs(alpha) <= 0.03

    # --------------------------------------------------------------------- #
    #                     Logistic model & ETA calculation                  #
    # --------------------------------------------------------------------- #

    def _logistic5(self, t, K, k, lam, D, gamma):
        """Five‑parameter logistic (5PL) in °F."""
        return D + (K - D) / ((1 + math.exp(-k * (t - lam))) ** gamma)

    def _update_model_estimate(self):
        """Fit Stage I logistic curve and compute ETA."""
        if curve_fit is None:
            return  # SciPy not available

        one_hour_ago = datetime.now() - timedelta(hours=1)
        stage1_pts = [(d['time'], d['meat'])
                      for d in self.temp_history
                      if d['time'] >= one_hour_ago and d['meat'] <= 150]

        if len(stage1_pts) < 15:
            return

        t0 = stage1_pts[0][0]
        t_hours = [(pt[0] - t0).total_seconds() / 3600 for pt in stage1_pts]
        temps   = [pt[1] for pt in stage1_pts]

        D_init   = temps[0]
        K_init   = self.target_meat
        k_init   = 1.0
        lam_init = t_hours[len(t_hours)//2]
        gamma_init = 1.0

        try:
            popt, _ = curve_fit(
                self._logistic5, t_hours, temps,
                p0=[K_init, k_init, lam_init, D_init, gamma_init],
                maxfev=8000
            )
            self.model_params = popt
            K, k, lam, D, gamma = popt

            self.eta_wrap = self.start_time + timedelta(
                hours=lam + (t0 - self.start_time).total_seconds()/3600)

            # inverse 5PL to solve for t when meat == target_meat
            target_T = self.target_meat
            if target_T < K:
                ratio = (K - D) / (target_T - D)
                t_target = lam - (1/k) * math.log(ratio ** (1/gamma) - 1)
                self.eta_finish = self.start_time + timedelta(
                    hours=t_target + (t0 - self.start_time).total_seconds()/3600)
            else:
                self.eta_finish = None

            # RMSE on full history
            full_t = [(d['time'] - t0).total_seconds()/3600
                      for d in self.temp_history]
            preds  = [self._logistic5(ti, *popt) for ti in full_t]
            full_y = [d['meat'] for d in self.temp_history]
            mse = sum((y - p) ** 2 for y, p in zip(full_y, preds)) / len(full_y)
            self.model_rmse = math.sqrt(mse)

        except Exception:
            pass  # silently ignore fit failures

    # --------------------------------------------------------------------- #
    #                        Display & conversation                         #
    # --------------------------------------------------------------------- #

    def get_temp_summary(self):
        if len(self.temp_history) < 2:
            return "no temp data yet"

        recent = list(self.temp_history)[-20:]
        pit_t   = [d['pit']  for d in recent]
        meat_t  = [d['meat'] for d in recent]

        pit_now, meat_now = pit_t[-1], meat_t[-1]
        pit_trend = pit_t[-1] - pit_t[0]
        meat_rate = (meat_t[-1] - meat_t[0]) * 3  # ≈°F/hr over 10 min

        ambient_str = f"{self.ambient_temp:.0f}°F" if self.ambient_temp else "Unknown"

        summary = (f"Temps: pit {pit_now:.0f}°F ({pit_trend:+.1f}/10 min), "
                   f"meat {meat_now:.0f}°F ({meat_rate:+.1f}°F/hr), "
                   f"ambient {ambient_str}")

        if self.eta_finish and self.eta_wrap:
            hrs_left = (self.eta_finish - datetime.now()).total_seconds()/3600
            rmse_str = f" RMSE {self.model_rmse:.1f}°F" if self.model_rmse else ""
            summary += (f" | ETA wrap {self.eta_wrap.strftime('%H:%M')}, "
                        f"finish {self.eta_finish.strftime('%H:%M')} "
                        f"({hrs_left:.1f} h){rmse_str}")
        return summary

    def handle_user_input(self, user_input):
        # Track user actions for context
        now = datetime.now()
        self.recent_user_actions.append({
            'time': now,
            'message': user_input.lower(),
            'input': user_input
        })
        
        # Track fuel-related mentions
        fuel_keywords = ['fuel', 'coal', 'wood', 'charcoal', 'briquette', 'added', 'add']
        if any(keyword in user_input.lower() for keyword in fuel_keywords):
            self.last_fuel_mention = now
            self.temp_recovery_in_progress = True
        
        msg = f"{user_input}\n\nCurrent: {self.get_temp_summary()}"
        print()
        print(f"\n🤖 {self._ask_claude(msg)}\n")

        # Save session after user interaction (important state change)
        self.save_session()

    # --------------------------------------------------------------------- #
    #                          Sensor / event loop                          #
    # --------------------------------------------------------------------- #

    def temp_reader_thread(self):
        try:
            proc = subprocess.Popen(
                ["rtl_433", "-F", "json"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                universal_newlines=True
            )
            for line in proc.stdout:
                try:
                    data = json.loads(line.strip())
                    model = data.get('model')
                    if model == 'Thermopro-TP12':
                        parsed = {
                            'time': datetime.strptime(data['time'], '%Y-%m-%d %H:%M:%S'),
                            'pit':  data['temperature_1_C'] * 9/5 + 32,
                            'meat': data['temperature_2_C'] * 9/5 + 32
                        }
                        self.data_queue.put(parsed)

                    elif model == 'LaCrosse-TX141Bv3':
                        self.ambient_temp = data['temperature_C'] * 9/5 + 32
                except json.JSONDecodeError:
                    pass
        except FileNotFoundError:
            print("rtl_433 not found. Is it installed and on PATH?")
        except Exception as e:
            print(f"rtl_433 died: {e}")

    def process_temp_update(self, data):
        self.temp_history.append(data)
        self.last_update = data['time']

        self._update_model_estimate()       # refresh logistic model

        now = datetime.now()
        cook_time = (now - self.start_time).total_seconds() / 3600
        status = f"[{data['time'].strftime('%H:%M')}] pit:{data['pit']:.0f}°F meat:{data['meat']:.0f}°F"
        if self.ambient_temp:
            status += f" outside:{self.ambient_temp:.0f}°F"
        status += f" | {cook_time:.1f} h"
        # Only display temps periodically to reduce noise
        if (self.last_display_time is None or 
            (now - self.last_display_time).total_seconds() >= self.display_interval):
            print(status)
            self.last_display_time = now
        else:
            # Show simple progress indicator between displays
            print(".", end="", flush=True)

        self.check_critical_conditions(data)

        # Proactive monitoring for gradual trends
        if (now - self.last_proactive_check).total_seconds() >= self.proactive_check_interval:
            self.check_gradual_trends(data)
            self.last_proactive_check = now

        # Auto-save session periodically
        if (self.last_save_time is None or
            (now - self.last_save_time).total_seconds() >= self.save_interval):
            self.save_session()
            self.last_save_time = now

    def run(self):
        temp_thread = threading.Thread(target=self.temp_reader_thread, daemon=True)
        temp_thread.start()

        print("Type to chat with Claude (or 'quit').  Examples:")
        print("  just added 10 briquettes")
        print("  wrapped the brisket")
        print("  windy AF today")
        print("-" * 50)

        while True:
            while not self.data_queue.empty():
                self.process_temp_update(self.data_queue.get())

            # non‑blocking stdin (POSIX only)
            try:
                import select
                if sys.stdin in select.select([sys.stdin], [], [], 0.1)[0]:
                    user_input = input().strip()
                    if user_input.lower() == 'quit':
                        return
                    if user_input:
                        self.handle_user_input(user_input)
            except Exception:
                pass

            if self.last_update and (datetime.now() - self.last_update).seconds > 300:
                print("\n⚠️  No temp data for 5 min – check the sensor")

# ================================ Session Management =======================

def get_session_filename(start_time=None):
    """Generate timestamped session filename."""
    if start_time is None:
        start_time = datetime.now()
    timestamp = start_time.strftime('%Y-%m-%d_%H%M%S')
    return f".bbq_session_{timestamp}.json"

def find_latest_session():
    """Find the most recent session file."""
    pattern = ".bbq_session_*.json"
    sessions = glob.glob(pattern)
    if not sessions:
        return None
    # Sort by filename (which includes timestamp)
    sessions.sort(reverse=True)
    return sessions[0]

def get_session_age(session_file):
    """Get age of session in hours from filename timestamp."""
    try:
        # Extract timestamp from filename: .bbq_session_2025-11-20_093015.json
        basename = os.path.basename(session_file)
        timestamp_str = basename.replace('.bbq_session_', '').replace('.json', '')
        session_time = datetime.strptime(timestamp_str, '%Y-%m-%d_%H%M%S')
        age = (datetime.now() - session_time).total_seconds() / 3600
        return age
    except (ValueError, IndexError):
        return None

def archive_old_sessions(max_age_hours=48):
    """Archive sessions older than max_age_hours to .bbq_archive/ directory."""
    pattern = ".bbq_session_*.json"
    sessions = glob.glob(pattern)

    archive_dir = ".bbq_archive"
    archived_count = 0

    for session_file in sessions:
        age = get_session_age(session_file)
        if age and age > max_age_hours:
            # Create archive directory if it doesn't exist
            if not os.path.exists(archive_dir):
                os.makedirs(archive_dir)

            # Move to archive
            dest = os.path.join(archive_dir, os.path.basename(session_file))
            shutil.move(session_file, dest)
            archived_count += 1
            print(f"📦 Archived old session: {os.path.basename(session_file)} ({age:.1f}h old)")

    return archived_count

def list_archived_sessions():
    """List all archived sessions."""
    archive_dir = ".bbq_archive"
    if not os.path.exists(archive_dir):
        return []

    pattern = os.path.join(archive_dir, ".bbq_session_*.json")
    sessions = glob.glob(pattern)
    sessions.sort(reverse=True)
    return sessions

def generate_session_mailto(session_file, recipient="gabe@signalnine.net"):
    """Generate mailto link to share session data for analysis."""
    import urllib.parse
    import base64

    try:
        with open(session_file, 'r') as f:
            session_data = f.read()

        # Get basic metadata for subject line
        data = json.loads(session_data)
        metadata = data.get('metadata', {})
        meat_type = metadata.get('meat_type', 'unknown')
        weight = metadata.get('weight', '?')
        start_time = data.get('start_time', 'unknown')

        subject = f"AI Pitmaster Session Data: {weight}lb {meat_type} ({start_time[:10]})"

        # Truncate data if too long for mailto (some email clients have limits)
        max_body_length = 8000
        if len(session_data) > max_body_length:
            body = (
                f"Session data for {weight}lb {meat_type}\n\n"
                f"(Session data too large for email - {len(session_data)} chars)\n"
                f"Please attach the session file: {session_file}\n\n"
                f"Or paste the first part:\n\n{session_data[:max_body_length]}\n\n..."
            )
        else:
            body = (
                f"Session data for {weight}lb {meat_type}\n\n"
                f"```json\n{session_data}\n```\n\n"
                f"Feel free to analyze this data to improve AI Pitmaster!"
            )

        mailto_url = f"mailto:{recipient}?subject={urllib.parse.quote(subject)}&body={urllib.parse.quote(body)}"

        return mailto_url

    except Exception as e:
        return f"Error generating mailto link: {e}"

def print_share_instructions():
    """Print instructions for sharing session data."""
    archived = list_archived_sessions()
    if not archived:
        print("\nNo archived sessions to share yet.")
        return

    print("\n" + "=" * 60)
    print("📊 Share Your Cook Data")
    print("=" * 60)
    print("\nHelp improve AI Pitmaster by sharing your archived sessions!")
    print(f"\nYou have {len(archived)} archived session(s):")

    for i, session in enumerate(archived[:5], 1):  # Show first 5
        basename = os.path.basename(session)
        try:
            with open(session, 'r') as f:
                data = json.load(f)
            metadata = data.get('metadata', {})
            print(f"  {i}. {metadata.get('meat_type', '?')} ({metadata.get('weight', '?')}lb) - {basename}")
        except:
            print(f"  {i}. {basename}")

    print("\nTo share your data:")
    print("  1. Run: python3 -c \"from ai_pitmaster import generate_session_mailto; print(generate_session_mailto('.bbq_archive/SESSION_FILE.json'))\"")
    print("  2. Open the generated mailto: link in your browser")
    print("  3. Send the email!")
    print("\nYour data helps improve stall detection, ETA predictions, and")
    print("cooking advice for everyone. Thank you!")
    print("=" * 60)

# ================================ main =====================================

def main():
    api_key = os.getenv('ANTHROPIC_API_KEY')
    if not api_key:
        print("Set ANTHROPIC_API_KEY env var")
        sys.exit(1)

    print("=== AI pitmaster ===")

    # Archive old sessions first
    archived_count = archive_old_sessions(max_age_hours=48)

    # Show sharing instructions if there are archived sessions
    if archived_count > 0:
        print_share_instructions()

    # Check for existing recent session
    session_file = find_latest_session()
    if session_file:
        age = get_session_age(session_file)
        if age is not None and age <= 48:
            try:
                with open(session_file, 'r') as f:
                    session_data = json.load(f)
                metadata = session_data['metadata']
                start_time = datetime.fromisoformat(session_data['start_time'])
                elapsed = (datetime.now() - start_time).total_seconds() / 3600

                print(f"\n📂 Found recent session:")
                print(f"   {metadata['weight']} lb {metadata['meat_type']}")
                print(f"   Started: {start_time.strftime('%Y-%m-%d %H:%M')}")
                print(f"   Age: {age:.1f} hours")
                print(f"   Temp readings: {len(session_data['temp_history'])}")

                restore = input("\nRestore this session? [Y/n]: ").strip().lower()
                if restore != 'n':
                    phone = os.getenv('BBQ_PHONE') or input("Phone # for SMS (blank to skip): ").strip()
                    if phone and not phone.startswith('+'):
                        phone = '+1' + phone

                    convo = ClaudeBBQConversation.load_session(api_key, session_file, phone or None)
                    if convo:
                        try:
                            convo.run()
                        except KeyboardInterrupt:
                            convo.save_session()
                            print("\n💾 Session saved. Bon appétit!")
                        return
            except Exception as e:
                print(f"⚠️  Could not read session file: {e}")
                print("Starting fresh session...\n")

    meat_type  = input("Meat type [brisket]: ").strip() or "brisket"
    weight     = float(input("Weight in lbs [12]: ").strip() or "12")
    target_pit = int(input("Target pit temp [225]: ").strip() or "225")
    target_meat= int(input("Target meat temp [203]: ").strip() or "203")

    phone = os.getenv('BBQ_PHONE') or input("Phone # for SMS (blank to skip): ").strip()
    if phone and not phone.startswith('+'):
        phone = '+1' + phone  # assume US

    print(f"\nStarting {weight} lb {meat_type} cook … rtl_433 will start automatically.\n")
    # Generate timestamped session filename
    session_filename = get_session_filename()

    convo = ClaudeBBQConversation(api_key, target_pit, target_meat,
                                  meat_type, weight, phone or None,
                                  session_file=session_filename)
    try:
        convo.run()
    except KeyboardInterrupt:
        convo.save_session()
        print("\n💾 Session saved. Bon appétit!")

if __name__ == "__main__":
    main()
