"""Authored, spoiler-progressive content for The Way of Kings companion site.

Every character bio is written as a sequence of stages, each keyed to the
chapter id where it first becomes true. When rendering chapter N, wok_model
picks — for each POV character in that chapter — the *latest* stage whose
starting chapter is at or before N in book order. This guarantees a bio can
never mention anything from a later chapter, because later stages simply
aren't reachable yet.

Chapter/POV/location data is compiled from the published book (chapter
order, POV assignments, settings). Character stage text is hand-written to
summarize only what's revealed by that point — no forward references.
"""

# --- Regions (for the map) -------------------------------------------------

REGIONS = [
    {"key": "shattered_plains", "label": "The Shattered Plains"},
    {"key": "kharbranth", "label": "Kharbranth"},
    {"key": "hearthstone", "label": "Hearthstone, Alethkar"},
    {"key": "alethkar_front", "label": "An Alethi warfront"},
    {"key": "kholinar", "label": "Kholinar, Alethkar"},
    {"key": "unclaimed_hills", "label": "The Unclaimed Hills"},
    {"key": "jah_keved", "label": "Jah Keved"},
    {"key": "reshi_isles", "label": "The Reshi Isles"},
    {"key": "emul", "label": "Emul"},
    {"key": "aimia", "label": "Aimia"},
    {"key": "shinovar", "label": "Near Shinovar"},
    {"key": "tashikk", "label": "Tashikk"},
]

# --- Characters --------------------------------------------------------
# Each stage is (from_chapter_id, display_name, blurb). Stages must be in
# book order. Keep it minimal — a bare "who + where", not a plot recap —
# and don't let display_name itself reveal something unrevealed yet (e.g. a
# character's name before the book actually gives it to the reader).

CHARACTERS = {
    "kaladin": {
        "stages": [
            ("ch1", "Kaladin", "Kaladin leads a small spear squad in a border skirmish. Experienced, and clearly troubled by something."),
            ("ch2", "Kaladin", "Kaladin is now a prisoner being transported somewhere under guard — a big change from the last chapter, not yet explained."),
            ("ch6", "Kaladin", "Kaladin has been put to hard labor in one of the army's work crews."),
            ("ch10", "Kaladin", "A flashback shows Kaladin as a boy, helping his father — a surgeon — with his work."),
            ("ch11", "Kaladin", "Kaladin has taken charge of his crew."),
            ("ch16", "Kaladin", "Kaladin grew up in a small town, torn between his father's wishes and a different path of his own."),
            ("ch37", "Kaladin", "As a teenager, Kaladin made a choice that set him firmly apart from his father's hopes for him."),
            ("ch46", "Kaladin", "Strange things have started happening around Kaladin that he can't explain."),
            ("ch57", "Kaladin", "Kaladin's crew has changed noticeably since he took charge."),
            ("ch73", "Kaladin", "Kaladin is starting to suspect he might be more than an ordinary soldier."),
        ],
    },
    "shallan": {
        "stages": [
            ("ch3", "Shallan", "Shallan has traveled to a foreign city seeking out a renowned scholar."),
            ("ch5", "Shallan", "Shallan is trying to convince the scholar to take her on as a student."),
            ("ch8", "Shallan", "Shallan has been accepted as the scholar's ward — though she seems to have a private motive beyond study."),
            ("ch33", "Shallan", "Shallan's private plan is now underway."),
            ("ch36", "Shallan", "Shallan has taken a significant, risky step toward her hidden goal."),
            ("ch60", "Shallan", "Shallan's situation has grown more dangerous."),
            ("ch70", "Shallan", "Shallan is recovering from something traumatic, and grieving."),
            ("ch72", "Shallan", "Shallan has come clean about her secret, and the two of them are now working together."),
        ],
    },
    "dalinar": {
        "stages": [
            ("ch12", "Dalinar", "Dalinar is a senior military leader, known for holding himself to a strict personal code."),
            ("ch18", "Dalinar", "Dalinar has been having strange episodes that worry the people around him."),
            ("ch22", "Dalinar", "Dalinar is trying to unite quarreling factions under his own leadership."),
            ("ch26", "Dalinar", "Dalinar's episodes seem to be guiding some of his decisions now."),
            ("ch52", "Dalinar", "Dalinar has offered to step back from leadership."),
            ("ch65", "Dalinar", "Dalinar has struck an uneasy alliance with a longtime rival."),
        ],
    },
    "adolin": {
        "stages": [
            ("ch12", "Adolin", "Adolin is a senior leader's eldest son — confident, socially active, and protective of his father."),
            ("ch58", "Adolin", "Adolin is trying to work out a rival's intentions toward his family."),
        ],
    },
    "szeth": {
        "stages": [
            ("prologue", "A masked figure", "A masked figure with unusual abilities carries out a killing that seems to trouble him deeply."),
            ("i3", "A masked figure", "This figure serves under a harsh, binding obligation to whoever currently controls him."),
            ("i9", "A masked figure", "He continues to be passed between masters, still used as a weapon against his will."),
        ],
    },
    "renarin": {
        "stages": [
            ("ch12", "Renarin", "Renarin is a senior leader's younger son — less suited to combat, and quietly wants to prove himself."),
        ],
    },
    "jasnah": {
        "stages": [
            ("ch5", "Jasnah", "Jasnah is the scholar Shallan has sought out — well known, and controversial for her views."),
        ],
    },
    "navani": {
        "stages": [
            ("ch22", "Navani", "Navani is an older, accomplished woman connected to the royal family, with her own reasons for being here."),
        ],
    },
    "sadeas": {
        "stages": [
            ("ch12", "Sadeas", "Sadeas is another senior military leader, with a long and complicated history with Dalinar."),
        ],
    },
    "elhokar": {
        "stages": [
            ("ch12", "Elhokar", "Elhokar is the young king, anxious about threats to his life."),
        ],
    },
    "wit": {
        "stages": [
            ("ch12", "The King's Wit", "The King's Wit is a newly arrived court jester who seems unusually perceptive."),
            ("ch54", "The King's Wit", "The Wit reveals a little more about himself before announcing he's leaving."),
        ],
    },
    "teft": {
        "stages": [
            ("ch11", "Teft", "Teft is one of the more experienced men in Kaladin's crew."),
        ],
    },
    "rock": {
        "stages": [
            ("ch14", "Rock", "Rock is a good-humored member of the crew, from a distant people."),
        ],
    },
    "moash": {
        "stages": [
            ("ch14", "Moash", "Moash is one of the more hardened, skeptical men in the crew."),
        ],
    },
    "gaz": {
        "stages": [
            ("ch6", "Gaz", "Gaz is the overseer in charge of the crew, more interested in his own gain than anyone's wellbeing."),
        ],
    },
    "syl": {
        "stages": [
            ("ch2", "A small spirit", "A small, sentient spirit has taken an unusual interest in Kaladin — most people don't even notice such things exist."),
            ("ch6", "Syl", "The spirit has given Kaladin a name for herself: Syl."),
        ],
    },
    "cenn": {
        "stages": [
            ("ch1", "Cenn", "Cenn is a nervous, inexperienced young soldier in Kaladin's squad."),
        ],
    },
    "nan_balat": {
        "stages": [
            ("i2", "Nan Balat", "Nan Balat is a relative of Shallan's, managing family matters back home while she's away."),
        ],
    },
    "ishikk": {
        "stages": [
            ("i1", "Ishikk", "Ishikk is a local fisherman asked by strangers to help find someone."),
        ],
    },
    "rysn": {
        "stages": [
            ("i4", "Rysn", "Rysn is a young trader learning the business from an older merchant."),
        ],
    },
    "axies": {
        "stages": [
            ("i5", "Axies", "Axies is a traveling scholar with an unusual specialty."),
        ],
    },
    "baxil": {
        "stages": [
            ("i7", "Baxil", "Baxil is caught up in a small-time job for a mysterious employer."),
        ],
    },
    "geranid": {
        "stages": [
            ("i8", "Geranid", "Geranid is a scholar carrying out careful research somewhere remote."),
        ],
    },
    "taravangian": {
        "stages": [
            ("ch5", "Taravangian", "Taravangian is the elderly ruler of the city Shallan has traveled to."),
        ],
    },
    "amaram": {
        "stages": [
            ("ch47", "Amaram", "Amaram is a commander Kaladin served under before — someone he clearly has complicated feelings about."),
        ],
    },
    "kabsal": {
        "stages": [
            ("ch7", "Kabsal", "Kabsal is a friendly religious figure who's struck up a rapport with Shallan."),
        ],
    },
    "kalak": {
        "stages": [
            ("prelude", "Kalak", "Kalak is one of several strange, long-lived figures gathering at an ancient, ruined site."),
        ],
    },
}

# --- Chapters ---------------------------------------------------------
# (id, number-or-None, label, part, pov[], location)

CHAPTERS = [
    {"id": "prelude", "number": None, "label": "Prelude", "part": "Prelude", "pov": ["kalak"], "location": None},
    {"id": "prologue", "number": None, "label": "Prologue", "part": "Prologue", "pov": ["szeth"], "location": "kholinar"},
    {"id": "ch1", "number": 1, "label": "Chapter 1", "part": "Part One", "pov": ["cenn", "kaladin"], "location": "alethkar_front"},
    {"id": "ch2", "number": 2, "label": "Chapter 2", "part": "Part One", "pov": ["kaladin", "syl"], "location": "unclaimed_hills"},
    {"id": "ch3", "number": 3, "label": "Chapter 3", "part": "Part One", "pov": ["shallan"], "location": "kharbranth"},
    {"id": "ch4", "number": 4, "label": "Chapter 4", "part": "Part One", "pov": ["kaladin"], "location": "unclaimed_hills"},
    {"id": "ch5", "number": 5, "label": "Chapter 5", "part": "Part One", "pov": ["shallan", "jasnah", "taravangian"], "location": "kharbranth"},
    {"id": "ch6", "number": 6, "label": "Chapter 6", "part": "Part One", "pov": ["kaladin", "gaz", "syl"], "location": "shattered_plains"},
    {"id": "ch7", "number": 7, "label": "Chapter 7", "part": "Part One", "pov": ["shallan", "kabsal"], "location": "kharbranth"},
    {"id": "ch8", "number": 8, "label": "Chapter 8", "part": "Part One", "pov": ["shallan", "jasnah"], "location": "kharbranth"},
    {"id": "ch9", "number": 9, "label": "Chapter 9", "part": "Part One", "pov": ["kaladin", "syl"], "location": "shattered_plains"},
    {"id": "ch10", "number": 10, "label": "Chapter 10", "part": "Part One", "pov": ["kaladin"], "location": "hearthstone"},
    {"id": "ch11", "number": 11, "label": "Chapter 11", "part": "Part One", "pov": ["kaladin", "syl", "gaz", "teft"], "location": "shattered_plains"},
    {"id": "i1", "number": None, "label": "Interlude 1", "part": "Interludes", "pov": ["ishikk"], "location": "tashikk"},
    {"id": "i2", "number": None, "label": "Interlude 2", "part": "Interludes", "pov": ["nan_balat"], "location": "jah_keved"},
    {"id": "i3", "number": None, "label": "Interlude 3", "part": "Interludes", "pov": ["szeth"], "location": "jah_keved"},
    {"id": "ch12", "number": 12, "label": "Chapter 12", "part": "Part Two", "pov": ["adolin", "dalinar", "renarin", "sadeas", "elhokar", "wit"], "location": "shattered_plains"},
    {"id": "ch13", "number": 13, "label": "Chapter 13", "part": "Part Two", "pov": ["adolin", "dalinar", "elhokar"], "location": "shattered_plains"},
    {"id": "ch14", "number": 14, "label": "Chapter 14", "part": "Part Two", "pov": ["kaladin", "moash", "gaz", "rock", "syl"], "location": "shattered_plains"},
    {"id": "ch15", "number": 15, "label": "Chapter 15", "part": "Part Two", "pov": ["adolin", "dalinar", "elhokar", "sadeas"], "location": "shattered_plains"},
    {"id": "ch16", "number": 16, "label": "Chapter 16", "part": "Part Two", "pov": ["kaladin"], "location": "hearthstone"},
    {"id": "ch17", "number": 17, "label": "Chapter 17", "part": "Part Two", "pov": ["kaladin", "gaz"], "location": "shattered_plains"},
    {"id": "ch18", "number": 18, "label": "Chapter 18", "part": "Part Two", "pov": ["adolin", "dalinar", "renarin"], "location": "shattered_plains"},
    {"id": "ch19", "number": 19, "label": "Chapter 19", "part": "Part Two", "pov": ["dalinar"], "location": "shattered_plains"},
    {"id": "ch20", "number": 20, "label": "Chapter 20", "part": "Part Two", "pov": ["kaladin"], "location": "hearthstone"},
    {"id": "ch21", "number": 21, "label": "Chapter 21", "part": "Part Two", "pov": ["kaladin", "syl", "gaz"], "location": "shattered_plains"},
    {"id": "ch22", "number": 22, "label": "Chapter 22", "part": "Part Two", "pov": ["dalinar", "adolin", "navani", "wit"], "location": "shattered_plains"},
    {"id": "ch23", "number": 23, "label": "Chapter 23", "part": "Part Two", "pov": ["kaladin", "rock", "teft"], "location": "shattered_plains"},
    {"id": "ch24", "number": 24, "label": "Chapter 24", "part": "Part Two", "pov": ["dalinar", "adolin"], "location": "shattered_plains"},
    {"id": "ch25", "number": 25, "label": "Chapter 25", "part": "Part Two", "pov": ["kaladin"], "location": "hearthstone"},
    {"id": "ch26", "number": 26, "label": "Chapter 26", "part": "Part Two", "pov": ["dalinar", "renarin"], "location": "shattered_plains"},
    {"id": "ch27", "number": 27, "label": "Chapter 27", "part": "Part Two", "pov": ["kaladin", "syl"], "location": "shattered_plains"},
    {"id": "ch28", "number": 28, "label": "Chapter 28", "part": "Part Two", "pov": ["adolin", "dalinar", "navani"], "location": "shattered_plains"},
    {"id": "i4", "number": None, "label": "Interlude 4", "part": "Interludes", "pov": ["rysn"], "location": "shinovar"},
    {"id": "i5", "number": None, "label": "Interlude 5", "part": "Interludes", "pov": ["axies"], "location": "aimia"},
    {"id": "i6", "number": None, "label": "Interlude 6", "part": "Interludes", "pov": ["szeth"], "location": "jah_keved"},
    {"id": "ch29", "number": 29, "label": "Chapter 29", "part": "Part Three", "pov": ["shallan"], "location": "kharbranth"},
    {"id": "ch30", "number": 30, "label": "Chapter 30", "part": "Part Three", "pov": ["kaladin", "gaz"], "location": "shattered_plains"},
    {"id": "ch31", "number": 31, "label": "Chapter 31", "part": "Part Three", "pov": ["kaladin"], "location": "hearthstone"},
    {"id": "ch32", "number": 32, "label": "Chapter 32", "part": "Part Three", "pov": ["kaladin"], "location": "shattered_plains"},
    {"id": "ch33", "number": 33, "label": "Chapter 33", "part": "Part Three", "pov": ["shallan"], "location": "kharbranth"},
    {"id": "ch34", "number": 34, "label": "Chapter 34", "part": "Part Three", "pov": ["kaladin", "teft"], "location": "shattered_plains"},
    {"id": "ch35", "number": 35, "label": "Chapter 35", "part": "Part Three", "pov": ["kaladin", "teft"], "location": "shattered_plains"},
    {"id": "ch36", "number": 36, "label": "Chapter 36", "part": "Part Three", "pov": ["shallan", "jasnah"], "location": "kharbranth"},
    {"id": "ch37", "number": 37, "label": "Chapter 37", "part": "Part Three", "pov": ["kaladin"], "location": "hearthstone"},
    {"id": "ch38", "number": 38, "label": "Chapter 38", "part": "Part Three", "pov": ["kaladin", "teft"], "location": "shattered_plains"},
    {"id": "ch39", "number": 39, "label": "Chapter 39", "part": "Part Three", "pov": ["shallan"], "location": "kharbranth"},
    {"id": "ch40", "number": 40, "label": "Chapter 40", "part": "Part Three", "pov": ["kaladin"], "location": "shattered_plains"},
    {"id": "ch41", "number": 41, "label": "Chapter 41", "part": "Part Three", "pov": ["kaladin"], "location": "hearthstone"},
    {"id": "ch42", "number": 42, "label": "Chapter 42", "part": "Part Three", "pov": ["shallan", "jasnah"], "location": "kharbranth"},
    {"id": "ch43", "number": 43, "label": "Chapter 43", "part": "Part Three", "pov": ["kaladin"], "location": "shattered_plains"},
    {"id": "ch44", "number": 44, "label": "Chapter 44", "part": "Part Three", "pov": ["kaladin"], "location": "hearthstone"},
    {"id": "ch45", "number": 45, "label": "Chapter 45", "part": "Part Three", "pov": ["shallan"], "location": "kharbranth"},
    {"id": "ch46", "number": 46, "label": "Chapter 46", "part": "Part Three", "pov": ["kaladin"], "location": "shattered_plains"},
    {"id": "ch47", "number": 47, "label": "Chapter 47", "part": "Part Three", "pov": ["kaladin", "amaram"], "location": "alethkar_front"},
    {"id": "ch48", "number": 48, "label": "Chapter 48", "part": "Part Three", "pov": ["shallan"], "location": "kharbranth"},
    {"id": "ch49", "number": 49, "label": "Chapter 49", "part": "Part Three", "pov": ["kaladin", "teft"], "location": "shattered_plains"},
    {"id": "ch50", "number": 50, "label": "Chapter 50", "part": "Part Three", "pov": ["shallan"], "location": "kharbranth"},
    {"id": "ch51", "number": 51, "label": "Chapter 51", "part": "Part Three", "pov": ["kaladin", "amaram"], "location": "alethkar_front"},
    {"id": "i7", "number": None, "label": "Interlude 7", "part": "Interludes", "pov": ["baxil"], "location": "emul"},
    {"id": "i8", "number": None, "label": "Interlude 8", "part": "Interludes", "pov": ["geranid"], "location": "reshi_isles"},
    {"id": "i9", "number": None, "label": "Interlude 9", "part": "Interludes", "pov": ["szeth"], "location": "jah_keved"},
    {"id": "ch52", "number": 52, "label": "Chapter 52", "part": "Part Four", "pov": ["dalinar", "adolin", "renarin"], "location": "shattered_plains"},
    {"id": "ch53", "number": 53, "label": "Chapter 53", "part": "Part Four", "pov": ["kaladin"], "location": "shattered_plains"},
    {"id": "ch54", "number": 54, "label": "Chapter 54", "part": "Part Four", "pov": ["dalinar", "wit"], "location": "shattered_plains"},
    {"id": "ch55", "number": 55, "label": "Chapter 55", "part": "Part Four", "pov": ["kaladin"], "location": "shattered_plains"},
    {"id": "ch56", "number": 56, "label": "Chapter 56", "part": "Part Four", "pov": ["dalinar"], "location": "shattered_plains"},
    {"id": "ch57", "number": 57, "label": "Chapter 57", "part": "Part Four", "pov": ["kaladin"], "location": "shattered_plains"},
    {"id": "ch58", "number": 58, "label": "Chapter 58", "part": "Part Four", "pov": ["adolin"], "location": "shattered_plains"},
    {"id": "ch59", "number": 59, "label": "Chapter 59", "part": "Part Four", "pov": ["kaladin", "teft"], "location": "shattered_plains"},
    {"id": "ch60", "number": 60, "label": "Chapter 60", "part": "Part Four", "pov": ["shallan"], "location": "kharbranth"},
    {"id": "ch61", "number": 61, "label": "Chapter 61", "part": "Part Four", "pov": ["dalinar", "navani", "renarin", "adolin"], "location": "shattered_plains"},
    {"id": "ch62", "number": 62, "label": "Chapter 62", "part": "Part Four", "pov": ["kaladin", "moash"], "location": "shattered_plains"},
    {"id": "ch63", "number": 63, "label": "Chapter 63", "part": "Part Four", "pov": ["kaladin"], "location": "shattered_plains"},
    {"id": "ch64", "number": 64, "label": "Chapter 64", "part": "Part Four", "pov": ["dalinar", "navani"], "location": "shattered_plains"},
    {"id": "ch65", "number": 65, "label": "Chapter 65", "part": "Part Four", "pov": ["dalinar", "sadeas"], "location": "shattered_plains"},
    {"id": "ch66", "number": 66, "label": "Chapter 66", "part": "Part Four", "pov": ["kaladin"], "location": "shattered_plains"},
    {"id": "ch67", "number": 67, "label": "Chapter 67", "part": "Part Four", "pov": ["kaladin"], "location": "shattered_plains"},
    {"id": "ch68", "number": 68, "label": "Chapter 68", "part": "Part Four", "pov": ["adolin", "dalinar"], "location": "shattered_plains"},
    {"id": "ch69", "number": 69, "label": "Chapter 69", "part": "Part Four", "pov": ["navani"], "location": "shattered_plains"},
    {"id": "ch70", "number": 70, "label": "Chapter 70", "part": "Part Four", "pov": ["shallan"], "location": "kharbranth"},
    {"id": "ch71", "number": 71, "label": "Chapter 71", "part": "Part Four", "pov": ["szeth", "taravangian"], "location": "kharbranth"},
    {"id": "ch72", "number": 72, "label": "Chapter 72", "part": "Part Five", "pov": ["shallan", "jasnah"], "location": "kharbranth"},
    {"id": "ch73", "number": 73, "label": "Chapter 73", "part": "Part Five", "pov": ["kaladin", "syl"], "location": "shattered_plains"},
    {"id": "ch74", "number": 74, "label": "Chapter 74", "part": "Part Five", "pov": ["shallan", "jasnah"], "location": "kharbranth"},
    {"id": "ch75", "number": 75, "label": "Chapter 75", "part": "Part Five", "pov": ["dalinar", "navani"], "location": "shattered_plains"},
    {"id": "epilogue", "number": None, "label": "Epilogue", "part": "Epilogue", "pov": ["wit"], "location": "kholinar"},
]
