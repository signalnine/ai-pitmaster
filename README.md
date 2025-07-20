# AI pitmaster
SDR + LLM + SMS + Meat + Fire

this uses an rtl-sdr dongle to read wireless bbq thermometer data and feeds it to Claude Sonnet 3.5 for realtime cooking advice. it's intended for use with charcoal or wood smokers but will probably work with anything as long as you tell Claude what you're working with. it'll alert you via SMS if you need to add fuel, adjust air intake, etc. it can also do a pretty decent estimate of when your cook will likely be done. interface is natural language CLI. 

## what it does

- reads temp data from Thermopro TP12 (or similar) wireless thermometers via rtl_433
- maintains conversation with claude about your cook, ask it anything
- texts you when things go sideways
- tracks ambient temp from whatever weather stations are nearby
- detects the stall using actual math: http://www.tlhiv.org/papers/1-33-T-SouthernBarbeque-TeacherVersion.pdf

## hardware you need

### sdr dongle
any rtl2832u based usb dongle. the rtl-sdr.com v4 is good, or grab a nooelec nesdr for a few bucks less.

### thermometer
thermopro TP12 is what i use. any 433mhz bbq thermometer that rtl_433 supports should work. check `rtl_433 -L` for the full list

the TP12 has two probes - one for pit temp, one for meat. broadcasts every ~12 seconds on 433.92mhz

## setup

### install rtl_433
```bash
# debian/ubuntu
sudo apt install rtl-433

# mac
brew install rtl_433

# or build from source
git clone https://github.com/merbanan/rtl_433.git
```

### python deps
```bash
pip install anthropic requests
```

### environment vars
```bash
export ANTHROPIC_API_KEY=sk-ant-whatever
export TXTBELT_KEY=your_txtbelt_key_if_you_want_texts
export BBQ_PHONE=+15555551234  # optional but recommended 
```

## usage

```bash
python3 ai-pitmaster.py
```

it'll ask for meat type, weight, target temps. then rtl_433 starts automatically and begins monitoring

### during the cook

type stuff to tell claude what's happening:
- "just added a chimney of kingsford"
- "wrapped in butcher paper"
- "beer #3"
- "windy af today"

claude remembers everything and adjusts advice accordingly

### alerts

automatic alerts for:
- pit temp crashes (< target - 75f)
- pit temp spikes (> target + 50f)  
- approaching stall (~150f)
- almost done (195-200f)
- done (target temp)

## troubleshooting

### no temp data
- check rtl_433 sees your thermometer: `rtl_433 -f 433.92M`
- if you see something like `Failed to open rtlsdr device #0` there's probably another rtl_433 process, sometimes it doesn't shutdown gracefully, so just `pkill` it
- make sure thermometer is on and transmitting
- move dongle closer or use better antenna

### claude being weird
adjust the temperature in `_ask_claude()`. it's at 0.5 for casual vibes but sometimes claude gets too creative

### texts not working
textbelt free tier is 1/day. get a key or just remove the phone number

## example output
```
[10:23] pit:225°F meat:147°F outside:72°F | 8.2hrs

wrapped it in pink paper

🤖 good timing on the wrap. you're right at stall territory. should power through 
in 2-3hrs now. maybe bump pit to 250 if you're in a hurry but 225 is fine

[10:24] pit:227°F meat:148°F outside:72°F | 4.2hrs
[10:24] pit:226°F meat:149°F outside:72°F | 4.2hrs
```

## notes

- logs everything to conversation history. could add file logging if you want
- the mathematical stall detection is overkill but it works really well 
- ambient temp from weather stations is surprisingly useful, you might need to adjust for your (or your neighbor's in my case) weather station model
- claude costs like $0.25 per cook at current prices, you could very easily port this to work on your locally hosted LLM or OpenAI or w/e
