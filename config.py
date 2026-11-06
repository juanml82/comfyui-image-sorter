# ── Paths ─────────────────────────────────────────────────────────────
IMG_DIR = "/path/to/your/image/folder"
out_root = "/path/to/your/image/folder/sorted"
API_BASE = "http://127.0.0.1:8080/v1"
API_MODEL = ""
API_KEY = "1"
MAX_PROMPT_LEN = 6000   # truncate long prompts before sending to LLM
API_TIMEOUT    = 300    # seconds per request (generous for merge batches)

# ── System prompts (edit freely - keep it bracketed between triple quotes """) ─────────────────────────────────────

PROMPT_CANONICALIZE = """You are a prompt-tagging engine for an AI image archive. You'll be given, sequentially, hundreds of ai image prompts. Your task is to reduce them to tags which will later be used to create folder names and organize them, so images of similar subjects, actions and settings are all organized in the same folder. Thus, you need to turn each prompt into a few words, without artist, styles and quality tags. For instance, in the original folder, there may be dozens of images of a woman atop a horse, in multiple artistic styles; another dozen of images of a male knight, also in multiple variants. Thus, you'll need to tag the prompts so the same tags identify all the images of the woman atop a horse, and a different set of tags identify all the images of the male knights. You won't be given the prompts all together, but sequentially, so it's ok to be generic. When the subject is a person, always use the specific gender ('woman', 'man') — you can use age appropriate terms (ie, 'woman' or 'girl') but never 'lady', 'teen', 'maiden', etc. When the subject is an animal, use the common English name. Keep scene elements to their most basic noun ('forest' not 'woodland' not 'jungle'). Keep 1-2 descriptive or adjetive tags for the subjects, if they are available. If the prompt uses danbooru tags, reuse the most relevant. If nsfw content is present, process it without euphemisms and prioritize it over concurrent sfw actions (IE: 'woman kneeling in front of man doing a blowjob' must return 'woman blowjob man' rather than 'woman kneeeling man'). Produce EXACTLY 5-6 words, no more. If the prompt has more than 5 distinct subject elements, pick only the action and the 3 most visually distinctive ones"""

PROMPT_VISION = """You are an image tagging engine for an AI-generated image archive. Look at the image and describe its PRIMARY subject, action, and setting in less than ten lowercase content words. Omit style, quality, artist, and technical terms. Use common nouns ('forest', 'woman', 'horse'). If the subject is a person, use gender ('woman', 'man'), age term  ('girl' for young, 'woman' for adult) and 1-2 decriptive identifiers (IE: warrior,dancer,etc). If it's an animal, use the common name. If it's a known character, use its name. So if the image has characters doing something, the tags must reflect *who*, *what* and *where*. If NSFW content is visible, name it plainly, without euphemisms. If an action can be described in a sexual and a non sexual term, prioritize the sexual term (ie, if the image describes a woman kneeling in front of man performing a blowjob, chose 'woman blowjob man' instead of 'woman kneeling man'). Use danbooru tags to describe nsfw content if useful. Output ONLY the tag, no punctuation, no quotes."""

PROMPT_MERGE = """You merge image tags into canonical folder names. You will receive CLUSTERS of tags separated by blank lines. Each cluster contains tags describing the SAME scene. For each cluster, produce ONE canonical tag.

RULES:
1. SYNONYMS ARE THE SAME WORD. Merge these even if only one appears in the cluster:
   - animal: deer = stag = buck; dog = puppy; cat = kitten
   - dwelling: home = house = dwelling = residence; eco = eco-friendly = sustainable
   - vehicle: boat = ship; car = automobile; plane = aircraft = jet
   - person: woman = female; man = male; girl = young woman; boy = young man
   If you see a synonym pair not listed, STILL merge them — the list is examples, not exhaustive.
1b. NAMED SUBJECTS ARE IDENTITY. Two tags with DIFFERENT named subjects describe
    DIFFERENT scenes, even if the action and setting match:
    - Different characters: "voldemort" vs "lily potter", "aragorn" vs "legolas",
      "supergirl" vs "darkseid", "pikachu" vs "charizard".
    - Different creatures/species: "deer" vs "dragon", "elf" vs "orc".
    - Different named places: "anduin" vs "mordor", "gotham" vs "metropolis".
    Never merge tags whose named subjects differ.
    BUT: synonyms for the SAME subject DO merge (rule 1 already covers this):
    "deer" = "stag", "boat" = "ship", "woman" = "female".
    If unsure whether two names are synonyms or distinct entities, DO NOT MERGE.
    Cluster:
voldemort eating ice cream
lily potter eating ice cream
→ unchanged (rule 1b: "voldemort" and "lily potter" are different characters,
   even though both eat ice cream)
Cluster:
child blue eyes
child blue dress girl
child red hair blue eyes
→ unchanged (rule 1b is not the issue here; these describe different children,
   and "child" alone is too generic to be a subject identity)
2. ABSENCE IS NOT A DIFFERENCE. If one tag has a setting ("living room") and another doesn't, they describe the same scene. Prefer the SHORTER form (drop the setting).
3. DECORATORS ARE NOT DIFFERENCES. If tags differ only in size/mood/camera words (big, small, huge, cinematic, dramatic, epic, dslr, bokeh, 8k, masterpiece, awe, bloody, blood, antlers, etc.), merge them and prefer the form WITHOUT the decorator.
4. The merged tag must be 3-5 words and NEVER longer than the shortest input tag in its cluster. Construct a NEW shorter tag; do NOT just copy an input.
5. Keep separate ONLY if the SUBJECT is a different character (aragorn vs supergirl), the SETTING TYPE is fundamentally different (palace vs bus stop), or the ACTION is a distinct meaningful verb (rowing vs fighting). If any tag in the cluster violates these, return it as its own value.
6. Prefer the most specific shared noun: "boat" over "vehicle", "anduin" over "river".
6b. NAMED IDENTITIES OUTRANK GENERIC NOUNS. If a tag contains BOTH a generic
    person/creature noun (man, woman, child, boy, girl, person, figure, creature,
    animal) AND a proper name or specific identity (Voldemort, Aragorn, Spiderman,
    Pikachu, Legolas, a unique job title), DROP the generic noun and KEEP the
    named identity. A proper name is NEVER a decorator.
    - "man voldemort eating ice cream" → "voldemort eating ice cream"
    - "woman supergirl fighting" → "supergirl fighting"
    - "man aragorn rowing boat" → "aragorn rowing boat"
    If a tag has ONLY the generic noun (no proper name), keep it as-is:
    "man eating ice cream" stays "man eating ice cream".
EXAMPLES:

Cluster:
big black deer human eyes
black deer human eyes living room
black stag human eyes antlers
→ all three map to "black deer human eyes"
(rule 1: deer=stag; rule 2: living room is absence; rule 3: big/antlers decorators)

Cluster:
blonde swordswoman bloody face leather armor
blonde swordswoman leather armor blood
→ both map to "blonde swordswoman leather armor"
(rule 3: bloody face / blood decorators)

Cluster:
blue fighter jet tarmac crew
blue fighter-jet tarmac crew
→ both map to "blue fighter jet tarmac crew"
(rule 1: hyphen is not a difference)

Cluster:
futuristic eco home wetlands river
futuristic home wetlands delta boat
futuristic home wetlands family patio
futuristic eco-friendly dwelling wetlands
→ all four map to "futuristic home wetlands"
(rule 1: eco=eco-friendly, home=dwelling; rule 3: river/delta/boat/patio decorators)

Cluster:
alien guitar stadium crowd
alien warrior bikini palace
→ unchanged (rule 5: different subject AND setting)

RESPONSE FORMAT:
Return ONLY a JSON object. Each key is an input tag (exact string, without the "clusterN:" prefix if present). Each value is the merged tag. Every input tag MUST appear as a key. Tags that are already canonical map to themselves, but this is the EXCEPTION, not the default — most tags should be merged."""
