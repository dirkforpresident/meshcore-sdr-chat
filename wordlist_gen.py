# SPDX-License-Identifier: GPL-3.0-or-later
# Channel-name wordlist generator (adapted from the author's own MeshCore monitor).
"""MeshCore SIGINT — Comprehensive Channel Name Wordlist

Generates 50,000+ candidate channel names for brute-forcing
MeshCore hashtag channel encryption. Combines:
- All German postal codes (5-digit)
- All German/Austrian/Swiss cities
- Ham radio callsign patterns
- Mesh/LoRa community terms
- Common German + English words
- Systematic pattern generation

Performance: Building the key table from this list takes <1s.
Lookup per packet is O(1) hash + ~400 HMAC checks = <1ms.
"""

import itertools
import string


def _generate_plz() -> list[str]:
    """Generate all German 5-digit postal codes (01000-99999).
    Also 2-digit PLZ regions and 3-digit areas."""
    codes = []
    # 2-digit PLZ regions
    for i in range(1, 100):
        codes.append(f"{i:02d}")
    # 3-digit PLZ areas
    for i in range(10, 999):
        codes.append(f"{i:03d}")
    # All 5-digit PLZ (01001-99998)
    for i in range(1000, 100000):
        codes.append(f"{i:05d}")
    return codes


def _generate_callsign_patterns() -> list[str]:
    """Generate German/Austrian/Swiss amateur radio callsign patterns.

    Generates all valid DACH callsigns (prefix + digit + 1-3 letter suffix).
    ~200 prefixes * 10 digits * (26 + 676 + 17576) suffixes = ~3.6M entries.
    Build time ~2-3s, memory ~200MB. Worth it for complete coverage.
    """
    calls = []
    # German prefixes (Class A + E + special)
    prefixes = ["da", "db", "dc", "dd", "de", "df", "dg", "dh",
                "di", "dj", "dk", "dl", "dm", "dn", "do", "dp", "dq", "dr"]
    # Austrian
    prefixes += ["oe"]

    for p in prefixes:
        calls.append(p)
        for d in range(10):
            calls.append(f"{p}{d}")
            # 1-letter suffix (DA1A etc.)
            for a in string.ascii_lowercase:
                calls.append(f"{p}{d}{a}")
                # 2-letter suffix (DO1XX etc.)
                for b in string.ascii_lowercase:
                    calls.append(f"{p}{d}{a}{b}")
                    # 3-letter suffix (DL6MRA etc.)
                    for c in string.ascii_lowercase:
                        calls.append(f"{p}{d}{a}{b}{c}")

    # HB9 prefix (Swiss — always 3-digit prefix)
    for a in string.ascii_lowercase:
        calls.append(f"hb9{a}")
        for b in string.ascii_lowercase:
            calls.append(f"hb9{a}{b}")
            for c in string.ascii_lowercase:
                calls.append(f"hb9{a}{b}{c}")

    return calls


def _generate_mesh_patterns() -> list[str]:
    """Generate systematic mesh/lora channel name patterns."""
    patterns = []
    bases = ["mesh", "lora", "node", "gw", "relay", "test", "ch",
             "kanal", "group", "grp", "net", "link", "funk"]
    cities_short = ["bs", "h", "hh", "k", "m", "s", "b", "f", "dd", "l",
                    "hb", "ks", "md", "goe", "hi", "wob", "sz", "gs", "gf",
                    "pe", "he", "ce", "bn", "ac", "hro", "hgw", "kl"]

    for base in bases:
        # mesh1, mesh2, ..., mesh99
        for n in range(1, 100):
            patterns.append(f"{base}{n}")
        # mesh-bs, mesh-h, etc.
        for city in cities_short:
            patterns.append(f"{base}-{city}")
            patterns.append(f"{base}{city}")
            patterns.append(f"{city}-{base}")
            patterns.append(f"{city}{base}")

    # Numbered channels
    for prefix in ["ch", "kanal", "channel", "group", "grp"]:
        for n in range(1, 50):
            patterns.append(f"{prefix}{n}")
            patterns.append(f"{prefix}-{n}")
            patterns.append(f"{prefix}_{n}")

    return patterns


def _generate_number_combos() -> list[str]:
    """Generate useful number combinations."""
    numbers = []
    # 1 to 9999
    for i in range(10000):
        numbers.append(str(i))
    # Common frequencies
    for f in ["433", "433.175", "433175", "868", "868.0", "915",
              "144", "144.800", "145", "145.500", "430", "439", "440",
              "446", "462", "pmr", "pmr446", "cb27", "freenet"]:
        numbers.append(f)
    return numbers


# =====================================================================
# STATIC WORDLISTS
# =====================================================================

# Complete list of German cities (Großstädte + Mittelstädte + bekannte Kleinstädte)
GERMAN_CITIES = [
    # Großstädte (>100k)
    "aachen", "augsburg", "bergisch gladbach", "berlin", "bielefeld",
    "bochum", "bonn", "bottrop", "braunschweig", "bremen",
    "bremerhaven", "chemnitz", "cottbus", "darmstadt", "dortmund",
    "dresden", "duesseldorf", "duisburg", "erfurt", "erlangen",
    "essen", "flensburg", "frankfurt", "freiburg", "fuerth",
    "gelsenkirchen", "goettingen", "guetersloh", "hagen", "halle",
    "hamburg", "hamm", "hannover", "heidelberg", "heilbronn",
    "herne", "hildesheim", "ingolstadt", "jena", "kaiserslautern",
    "karlsruhe", "kassel", "kiel", "koblenz", "koeln",
    "krefeld", "leipzig", "leverkusen", "luebeck", "ludwigshafen",
    "magdeburg", "mainz", "mannheim", "moenchengladbach", "moers",
    "muelheim", "muenchen", "muenster", "neuss", "nuernberg",
    "oberhausen", "offenbach", "oldenburg", "osnabrueck", "paderborn",
    "pforzheim", "potsdam", "recklinghausen", "regensburg", "remscheid",
    "reutlingen", "rostock", "saarbruecken", "salzgitter", "schwerin",
    "siegen", "solingen", "stuttgart", "trier", "ulm",
    "wiesbaden", "wolfsburg", "wuerzburg", "wuppertal", "zwickau",

    # Mittelstädte (50k-100k)
    "aalen", "arnsberg", "aschaffenburg", "bad homburg", "bad salzuflen",
    "bad oeynhausen", "bamberg", "bayreuth", "bergheim", "bocholt",
    "brandenburg", "castrop-rauxel", "celle", "coburg",
    "delmenhorst", "dessau", "detmold", "dinslaken", "dorsten",
    "dueren", "emden", "esslingen", "euskirchen", "friedrichshafen",
    "fulda", "garbsen", "gera", "giessen", "gladbeck",
    "goeppingen", "goslar", "grevenbroich", "greifswald", "gummersbach",
    "halberstadt", "hameln", "hanau", "hattingen", "heidenheim",
    "helmstedt", "herford", "herten", "hilden", "hof",
    "ibbenbüren", "iserlohn", "kerpen", "kempten",
    "konstanz", "landshut", "langenhagen", "limburg", "lingen",
    "lippstadt", "loerrach", "ludwigsburg", "luedenscheid", "lueneburg",
    "luenen", "marburg", "marl", "meerbusch", "menden",
    "minden", "neumuenster", "neustadt", "neubrandenburg",
    "neuwied", "nordhorn", "nordhausen", "offenburg",
    "passau", "peine", "pirmasens", "plauen", "pulheim",
    "ratingen", "ravensburg", "rheda-wiedenbrueck", "rheine",
    "rosenheim", "ruesselsheim", "schwerte", "sindelfingen",
    "soest", "speyer", "stade", "stralsund", "straubing",
    "troisdorf", "tuebingen", "uelzen", "unna",
    "velbert", "viersen", "villingen-schwenningen",
    "waiblingen", "weimar", "wesel", "wetzlar",
    "wilhelmshaven", "wismar", "witten", "wolfenbuettel",
    "worms",

    # Bekannte Kleinstädte / relevante Orte
    "ahrensburg", "alfeld", "alsfeld", "altena", "alzey",
    "amberg", "andernach", "anklam", "annaberg", "apolda",
    "attendorn", "aurich", "backnang", "bad bentheim",
    "bad harzburg", "bad hersfeld", "bad kreuznach", "bad nauheim",
    "bad pyrmont", "bad segeberg", "bad wildungen",
    "balingen", "bautzen", "beckum", "berchtesgaden",
    "bernburg", "biberach", "bingen", "bitburg", "blankenburg",
    "blomberg", "boppard", "brakel", "bueckeburg", "buende",
    "burg", "burgdorf", "buxtehude",
    "calw", "clausthal", "cloppenburg", "crailsheim", "cuxhaven",
    "daun", "diepholz", "dippoldiswalde", "donauwoerth",
    "eberswalde", "eckernfoerde", "einbeck", "eisenach",
    "elmshorn", "emmerich", "ennepetal", "erding",
    "eschwege", "ettlingen", "eutin",
    "fehmarn", "forchheim", "frankenberg", "freudenstadt",
    "fritzlar", "gandersheim", "gardelegen",
    "geesthacht", "geilenkirchen", "geldern", "gelnhausen",
    "genthin", "giengen", "glueckstadt", "goch",
    "goerlitz", "goslar", "grabow", "grafenau",
    "greiz", "grimma", "gross-gerau", "gruenberg",
    "hagenow", "haldensleben", "haltern", "hameleln",
    "hammelburg", "hann muenden", "harburg", "harzgerode",
    "havelberg", "helmstedt", "hennef", "herborn",
    "herzberg", "hessisch-lichtenau", "hettstedt", "heusenstamm",
    "hoexter", "hofgeismar", "holzminden", "homberg",
    "husum", "idar-oberstein", "ilmenau", "itzehoe",
    "jever", "juelich", "kamen", "kelheim",
    "kirchhain", "kissingen", "koenigslutter",
    "kyritz", "laer", "lage", "lamspringe",
    "lauenburg", "lauterbach", "leer", "lehrte",
    "lemgo", "lengerich", "leonberg", "lichtenfels",
    "lilienthal", "lindau", "loebau", "lohne",
    "luechow", "luedge", "lutherstadt wittenberg",
    "malchin", "marienberg", "marne", "meiningen",
    "meissen", "melle", "melsungen", "meppen",
    "merseburg", "meschede", "mettmann", "moelln",
    "muecheln", "muehlhausen", "muellheim",
    "muensingen", "nauen", "naumburg", "neuruppin",
    "nienburg", "norden", "northeim", "oberursel",
    "ochtrup", "oelde", "oerlinghausen", "osterode",
    "otterndorf", "parchim", "perleberg",
    "pinneberg", "plettenberg", "ploen", "prenzlau",
    "pritzwalk", "quedlinburg",
    "radeberg", "ratzeburg", "rendsburg", "rinteln",
    "riesa", "rochlitz", "rotenburg", "rothenburg",
    "rudolstadt", "saalfeld", "sangerhausen", "sassnitz",
    "schmalkalden", "schoeningen", "schoeppenstedt",
    "schorndorf", "schwabach", "schwarzenberg",
    "sebnitz", "seesen", "siegburg", "soltau",
    "sondershausen", "sonneberg", "springe",
    "stadthagen", "stassfurt", "steinheim", "stendal",
    "stolberg", "suhl", "syke",
    "teterow", "torgau", "tuttlingen",
    "ueberlingen", "uslar",
    "varel", "vechta", "verden", "vlotho",
    "warendorf", "warstein", "wedel",
    "weissenfels", "werdau", "wernigerode",
    "wertheim", "wesseling", "wittmund",
    "witzenhausen", "worbis", "wunstorf",
    "zeitz", "zerbst", "zittau", "zossen",

    # Braunschweig Ortsteile + Umgebung
    "querum", "riddagshausen", "stiddien", "volkmarode",
    "dibbesdorf", "bienrode", "waggum", "bevenrode",
    "hondelage", "wendhausen", "essenrode", "flechtorf",
    "grassel", "harvesse", "cremlingen", "lehre",
    "meine", "wendeburg", "vechelde", "lengede",
    "thiede", "watenbuetel", "melverode", "stoeckheim",
    "heidberg", "weststadt", "nordstadt", "suedstadt",
    "magniviertel", "ringgleis", "oestlichesringgebiet",
    "lamme", "broitzem", "rueningen", "mascherode",
    "schapen", "timmerlah", "gartenstadt", "siegfriedviertel",
    "bebelhof", "viewegsgarten", "leiferde", "rautheim",
    "schandelah", "erkerode", "ampleben", "lucklum",
    "destedt", "hemkenrode", "hordorf", "weddel",
    "bettmar", "lesse", "klein schöppenstedt",

    # Hannover Stadtteile
    "linden", "vahrenwald", "list", "suedstadt", "nordstadt",
    "bothfeld", "langenhagen", "isernhagen", "laatzen",
    "hemmingen", "ronnenberg", "gehrden", "barsinghausen",
    "wennigsen", "springe", "pattensen", "seelze",
    "garbsen", "wunstorf", "neustadt am ruebenberge",

    # Berliner Bezirke
    "mitte", "friedrichshain", "kreuzberg", "prenzlauerberg",
    "pankow", "charlottenburg", "wilmersdorf", "spandau",
    "steglitz", "zehlendorf", "tempelhof", "schoeneberg",
    "neukoelln", "treptow", "koepenick", "marzahn",
    "hellersdorf", "lichtenberg", "reinickendorf",
    "wedding", "moabit", "tiergarten",

    # Hamburger Stadtteile + Bezirke (komplett)
    "altona", "wandsbek", "eimsbuettel", "harburg",
    "bergedorf", "blankenese", "ottensen", "sternschanze",
    "stpauli", "st-pauli", "hafencity", "wilhelmsburg", "finkenwerder",
    "barmbek", "winterhude", "uhlenhorst", "eppendorf",
    "eidelstedt", "stellingen", "lokstedt", "niendorf",
    "schnelsen", "langenhorn", "fuhlsbuettel", "ohlsdorf",
    "alsterdorf", "poppenbüttel", "poppenbuettel", "wellingsbüttel",
    "sasel", "volksdorf", "farmsen", "bramfeld", "rahlstedt",
    "tonndorf", "jenfeld", "billstedt", "horn", "hamm-nord",
    "hamm-sued", "borgfelde", "hohenfelde", "eilbek",
    "marienthal", "lohbruegge", "neuallermöhe", "allermöhe",
    "neugraben", "fischbek", "hausbruch", "moorburg",
    "cranz", "francop", "neuenfelde", "altenwerder",
    "veddel", "rothenburgsort", "hammerbrook", "klostertor",
    "neustadt", "georgswerder", "ochsenwerder", "spadenland",
    "tatenberg", "moorfleet", "billbrook", "billwerder",
    "lurup", "osdorf", "iserbrook", "suelldorf", "rissen",
    "nienstedten", "gross-flottbek", "othmarschen",
    "bahrenfeld", "gross-borstel", "hummelsbüttel",
    "duvenstedt", "lemsahl", "bergstedt", "wohldorf",
    "neuengamme", "curslack", "altengamme", "kirchwerder",
    "reitbrook", "allermöhe", "lohbrügge", "wentorf",
    "glinde", "reinbek", "geesthacht", "schwarzenbek",
    "pinneberg", "elmshorn", "wedel", "uetersen",
    "schenefeld", "halstenbek", "rellingen",
    "norderstedt", "henstedt-ulzburg", "kaltenkirchen",
    "quickborn", "bad-bramstedt", "neumünster",
    "ahrensburg", "grosshansdorf", "trittau", "bargteheide",
    "bad-oldesloe", "reinfeld", "stormarn",
    "buxtehude", "stade", "jork", "altes-land", "altesland",
    "cuxhaven", "bremerhaven", "nordenham",
    "lüneburg", "lueneburg", "uelzen", "celle", "soltau",
    "buchholz", "tostedt", "winsen", "seevetal",
    "hamburg-nord", "hamburg-sued", "hamburg-ost", "hamburg-west",
    "grindel", "grindelallee", "grindelviertel", "karoviertel",
    "schanzenviertel", "reeperbahn", "landungsbruecken",
    "jungfernstieg", "gaensemarkt", "dammtor",
    "hauptbahnhof", "hbf", "alster", "binnenalster",
    "aussenalster", "elbe", "landungsbrücken",
    # Hamburger Institutionen / Locations
    "ccchh", "ccchamburg", "attraktor", "frappant",
    "gaengeviertel", "rote-flora", "rotflora", "fabrique",
    "knust", "logo", "molotow", "uebel-und-gefaehrlich",
    "mojo", "gruenspan", "docks", "grosse-freiheit",
    "miniatur-wunderland", "elbphilharmonie", "elphi",
    "speicherstadt", "kontorhausviertel", "hafen",
    "fischmarkt", "michel", "rathaus", "planten-un-blomen",

    # Braunschweig / Funkberg Empfangsbereich
    "braunschweig", "wolfsburg", "salzgitter", "peine",
    "gifhorn", "helmstedt", "wolfenbuettel", "goslar",
    "clausthal", "bad-harzburg", "wernigerode", "quedlinburg",
    "halberstadt", "magdeburg", "hildesheim", "hannover",
    "celle", "uelzen", "luechow", "dannenberg",
    "funkberg", "fernmeldeturm", "broitzem", "lehndorf",
    "riddagshausen", "querum", "waggum", "bienrode",
    "volkmarode", "lamme", "watenbüttel", "veltenhof",
    "rüningen", "stöckheim", "melverode", "mascherode",
    "cremlingen", "sickte", "schöppenstedt", "königslutter",
    "schöningen", "jerxheim", "grasleben", "velpke",
    "meinersen", "isenbüttel", "sassenburg", "wittingen",
    "brome", "rühen", "vorsfelde", "fallersleben",
    "harz", "brocken", "oberharz", "nordharz",
    "elm", "asse", "oker", "okerstausee",

    # München Stadtteile
    "schwabing", "bogenhausen", "sendling", "laim",
    "pasing", "aubing", "trudering", "riem",
    "giesing", "haidhausen", "lehel", "maxvorstadt",
    "neuhausen", "nymphenburg", "thalkirchen",
]

# Austrian cities
AUSTRIAN_CITIES = [
    "wien", "graz", "linz", "salzburg", "innsbruck", "klagenfurt",
    "villach", "wels", "steyr", "dornbirn", "bregenz", "wiener neustadt",
    "leonding", "klosterneuburg", "amstetten", "kapfenberg", "hallein",
    "moedling", "traiskirchen", "schwechat", "braunau", "stockerau",
    "saalfelden", "ansfelden", "tulln", "hohenems", "perchtoldsdorf",
    "feldkirch", "bludenz", "woergl", "kufstein", "lienz",
    "spittal", "voelkermarkt", "wolfsberg", "judenburg", "leoben",
    "bruck", "eisenstadt", "oberwart", "guessing", "zwettl",
    "krems", "stpoelten", "waidhofen", "mistelbach", "hollabrunn",
    "gmunden", "voecklabruck", "ried", "schaerding", "grieskirchen",
    "rohrbach", "freistadt", "perg",
]

# Swiss cities
SWISS_CITIES = [
    "zuerich", "genf", "basel", "bern", "lausanne", "winterthur",
    "luzern", "stgallen", "lugano", "biel", "thun", "aarau",
    "schaffhausen", "chur", "fribourg", "neuenburg", "sion",
    "emmen", "uster", "davos", "interlaken", "zug", "solothurn",
    "olten", "frauenfeld", "wil", "rapperswil", "arbon",
    "herisau", "appenzell", "glarus", "sarnen", "stans",
    "altdorf", "schwyz", "liestal", "delemont", "bellinzona",
    "locarno", "mendrisio", "martigny", "montreux", "vevey",
    "nyon", "morges", "renens", "yverdon", "payerne",
]

# Dutch/Belgian cities (cross-border mesh)
DUTCH_BELGIAN_CITIES = [
    "amsterdam", "rotterdam", "denhaag", "utrecht", "eindhoven",
    "groningen", "tilburg", "almere", "breda", "nijmegen",
    "enschede", "haarlem", "arnhem", "zaanstad", "amersfoort",
    "apeldoorn", "hertogenbosch", "maastricht", "leiden", "dordrecht",
    "zoetermeer", "zwolle", "deventer", "delft", "alkmaar",
    "heerlen", "venlo", "leeuwarden", "hilversum", "oss",
    "schiedam", "vlaardingen", "helmond", "purmerend", "roermond",
    "middelburg", "vlissingen", "terneuzen", "goes",
    # Belgian
    "antwerpen", "brussel", "gent", "luettich", "charleroi",
    "brugge", "namur", "leuven", "mechelen", "aalst",
    "hasselt", "kortrijk", "tournai", "mouscron", "oostende",
]

# German regions, landscapes, mountains, rivers
GEOGRAPHY = [
    # Bundeslaender (voll)
    "baden-wuerttemberg", "badenwuerttemberg", "bayern", "berlin",
    "brandenburg", "bremen", "hamburg", "hessen",
    "mecklenburg-vorpommern", "mecklenburgvorpommern",
    "niedersachsen", "nordrhein-westfalen", "nordrheinwestfalen",
    "rheinland-pfalz", "rheinlandpfalz", "saarland",
    "sachsen", "sachsen-anhalt", "sachsenanhalt",
    "schleswig-holstein", "schleswigholstein", "thueringen",
    # Abkuerzungen
    "bw", "by", "be", "bb", "hb", "hh", "he", "mv", "ni", "nds",
    "nrw", "rp", "rlp", "sl", "sn", "st", "sa", "sh", "th",
    # Regionen
    "allgaeu", "alpen", "bayerischerwald", "bergischesland", "bodensee",
    "boerde", "dach", "eifel", "elbe-weser", "elbmarsch", "emsland",
    "erzgebirge", "franken", "friesland", "havelland",
    "holsteinischeschweiz", "hunsrueck", "lausitz",
    "lueneburgerheide", "mark", "mittelrhein",
    "muensterland", "niederbayern", "norddeutschland", "nordsee",
    "oberbayern", "oberfranken", "oberpfalz", "oberschwaben", "ostalb",
    "ostfriesland", "ostwestfalen", "owl", "pfalz", "prignitz",
    "rheinland", "rhein-main", "rheinmain", "ruhrgebiet", "ruhrpott",
    "sauerland", "schwaben", "schwarzwald", "siegerland", "spreewald",
    "sueddeutschland", "suedwestfalen", "taunus", "uckermark",
    "vogtland", "voralpen", "vorpommern", "westerwald", "wetterau",
    "oberlausitz", "ostsee", "nordseeinseln", "ostfriesischeinseln",
    "halligen", "niederrhein", "bergstrasse", "oderbruch", "wendland",
    "altmark", "flaemung", "schorfheide", "maerkischeschweiz",
    "spessart", "hassberge", "steigerwald", "maintal",
    # Berge/Gebirge
    "brocken", "deister", "elm", "feldberg", "fichtelberg",
    "fichtelgebirge", "harz", "hornisgrinde", "kahlerasten",
    "koenigsstuhl", "kyffhaeuser", "langenberg", "odenwald",
    "petersberg", "rhoen", "rothaargebirge", "siebengebirge",
    "solling", "sonnenberg", "teutoburgerwald", "thueringerwald",
    "torfhaus", "vogelsberg", "wasserkuppe", "watzmann",
    "weserbergland", "wurmberg", "zugspitze",
    "grosserarber", "schneekopf", "inselsberg", "dolmar",
    "hohermeissner", "knuell", "kellerwald", "edersee",
    "moehnesee", "biggesee", "sorpesee", "hennesee",
    # Fluesse
    "aller", "donau", "elbe", "ems", "fulda", "isar", "lahn", "lech",
    "leine", "lippe", "main", "mosel", "nahe", "neckar", "oder",
    "oker", "rhein", "ruhr", "saar", "saale", "spree", "trave",
    "werra", "weser", "wuemme", "wupper", "isar", "inn", "salzach",
    "enns", "drau", "mur", "aare", "reuss", "limmat", "thur",
    # Inseln
    "sylt", "foehr", "amrum", "pellworm", "helgoland",
    "borkum", "juist", "norderney", "baltrum", "langeoog",
    "spiekeroog", "wangerooge", "ruegen", "usedom", "hiddensee",
    "fehmarn", "poel", "mainau", "reichenau", "lindau",
    # Seen
    "bodensee", "chiemsee", "ammersee", "starnbergersee",
    "tegernsee", "koenigssee", "walchensee", "mueritz",
    "schweriner see", "plauersee", "steinhuder meer",
    "edersee", "biggesee", "sorpesee", "moehnesee",
    # Nationalparks
    "wattenmeer", "berchtesgaden", "bayerischerwald",
    "saechsischeschweiz", "jasmund", "mueritz",
    "eifel", "kellerwald", "hainich", "schwarzwald",
    "hunsrueck-hochwald",
    # Land codes
    "de", "at", "ch", "eu", "nl", "be", "fr", "pl", "cz", "dk",
    "se", "no", "fi", "it", "es", "gb", "uk", "us", "dach",
    "europa", "europe", "germany", "deutschland", "oesterreich",
    "schweiz", "austria", "switzerland",
]

# Ham radio terms (comprehensive)
HAM_RADIO = [
    # Modes and protocols
    "aprs", "cw", "morsen", "fm", "am", "ssb", "lsb", "usb",
    "ft8", "ft4", "js8call", "js8", "psk31", "psk63",
    "rtty", "sstv", "ax25", "packet", "winlink", "vara",
    "dstar", "dmr", "c4fm", "fusion", "tetra", "pocsag",
    "wspr", "fldigi", "wsjt", "mfj", "digital", "analog",
    "adsb", "acars", "rtlsdr", "sdr", "hackrf", "airspy",
    # Bands and frequencies
    "2m", "70cm", "23cm", "13cm", "10m", "11m", "12m", "15m",
    "17m", "20m", "30m", "40m", "60m", "80m", "160m",
    "vhf", "uhf", "shf", "ehf", "hf", "mf", "lf", "vlf",
    "kurzwelle", "ukw", "mittelwelle", "langwelle",
    "433", "433mhz", "868", "868mhz", "915", "915mhz",
    "144", "144mhz", "145", "145mhz", "430", "430mhz",
    "440", "440mhz", "1296", "2400", "5800",
    "pmr", "pmr446", "freenet", "cb", "cb27", "ism",
    # Q-codes
    "qso", "qrp", "qrv", "qth", "qsl", "qrz", "qrm", "qrn",
    "qro", "qrt", "qsy", "qsb",
    # Common terms
    "cq", "cqde", "dx", "73", "72", "55", "88",
    "roger", "wilco", "over", "out", "break",
    "hamnet", "hamradio", "ham", "radio", "funk",
    "antenne", "antenna", "yagi", "dipol", "dipole",
    "beam", "vertical", "groundplane", "magloop",
    "repeater", "relais", "digipeater", "echolink",
    "allstar", "svxlink", "simplex", "duplex",
    "empfaenger", "sender", "transceiver", "handfunke",
    "portabel", "mobil", "stationaer", "fieldday",
    "amateurfunk", "funkamateure", "funkrunde",
    "funkturm", "sendeanlage", "notfunk", "notfunkgruppe",
    "ov", "ortsverband", "distrikt", "darc", "vfdb",
    "oevs", "uska", "veron", "arrl", "iaru",
    "rufzeichen", "callsign", "prefix", "suffix",
    "klubstation", "sonderrufzeichen",
    "fieldday", "contest", "dxpedition", "activator",
    "chaser", "sota", "pota", "wwff", "iota",
    "sat", "satellite", "iss", "ariss", "amsat",
    "emcomm", "ares", "races", "skywarn",
    # Equipment brands
    "yaesu", "icom", "kenwood", "motorola", "baofeng",
    "elecraft", "flexradio", "alinco", "wouxun",
    "anytone", "tyt", "retevis", "quansheng",
]

# Mesh/LoRa terms (comprehensive)
MESH_LORA = [
    # Core terms
    "meshtastic", "meshcore", "meshcom", "meshnet", "mesh",
    "letsmesh", "letsmeshde", "freemesh", "openmesh",
    "lora", "lorawan", "loramesh", "loranet", "loralink",
    "semtech", "chirp", "chirpstack",
    # Hardware
    "heltec", "heltecv3", "heltecv4", "rak", "rak4631", "rak19007",
    "rak2560", "rak11200", "lilygo", "tbeam", "tbeamv1",
    "techo", "tdeck", "twatch", "cubecell", "wisblock",
    "nrf52", "nrf52840", "esp32", "esp32s3", "esp32c3",
    "stm32", "rp2040", "pico", "teensy",
    "sx1262", "sx1276", "sx1278", "sx1280", "lr1110",
    "ebyte", "e22", "e32", "e220",
    # Software/community
    "meshpi", "meshmap", "meshbot", "meshgw", "meshcli",
    "rangetest", "traceroute", "nodeinfo",
    "gateway", "gw", "node", "relay", "router",
    "client", "sensor", "tracker", "base", "basestation",
    "bridge", "hub", "access", "ap",
    "meshastic", "lora32", "loraham", "hamesh",
    "meshmon", "meshview", "meshdash",
    # Operational
    "channel", "kanal", "group", "broadcast",
    "default", "primary", "secondary",
    "longfast", "longslow", "mediumfast", "mediumslow",
    "shortfast", "shortslow",
    "admin", "position", "telemetry", "serial",
    "rangetest", "storeforward", "neighborinfo",
    "detection", "paxcounter", "environmental",
]

# Common German words likely used as channel names
GERMAN_WORDS = [
    "allgemein", "alle", "anfaenger", "arbeit", "austausch",
    "basteln", "berg", "berge", "bier", "boot", "bruecke",
    "buero", "buerger",
    "chaos", "club", "community",
    "daheim", "dorf", "draussen",
    "ehrenamt", "einsatz", "energie",
    "familie", "feuer", "feuerwehr", "flohmarkt", "fluss",
    "forst", "fragen", "freifunk", "freunde", "frieden",
    "funk", "funkberg", "funkfeuer", "funker", "funktrupp",
    "garten", "gasthaus", "geheim", "gemeinde", "gipfel",
    "grenze", "gruppe", "gruss",
    "hafen", "heim", "helfer", "hilfe", "hobby",
    "hobbyfunk", "hof",
    "info", "insel",
    "jagd", "jugend",
    "kaffee", "kanal", "keller", "kirche", "kneipe",
    "krise", "kreis", "kueste",
    "lager", "land", "leben", "leute", "licht", "lokal",
    "markt", "mauer", "melde", "melder", "meldung",
    "morgen", "muehle",
    "nacht", "nachricht", "nachbar", "nachbarn", "natur",
    "netz", "netzwerk", "neu", "notruf", "notfall",
    "oben", "offroad", "ortsgruppe",
    "park", "pfad", "platz", "probe", "projekt",
    "regen", "region", "rettung", "ruf", "ruhe", "runde",
    "schule", "schutz", "see", "segler", "sicherheit",
    "signal", "sonne", "spiel", "stamm", "stammtisch",
    "stadt", "strand", "strom", "sturm",
    "tal", "technik", "test", "testen", "treff",
    "treffpunkt", "truppe", "turm",
    "ufer", "umgebung", "umwelt",
    "verein", "verkehr", "volk",
    "wald", "wache", "wasser", "weg", "wetter",
    "wiese", "wind", "winter", "wissen",
    "zentral", "zentrum", "zuhause",
    "abenteuer", "absturz", "alm", "anlage", "aufklaerung",
    "ausflug", "aussicht", "bagger", "bahn", "bahnhof",
    "baum", "beispiel", "blitz", "donner", "drehkreuz",
    "durchsage", "eisenbahn", "empfang", "ende", "ernte",
    "fahrt", "falle", "fernsicht", "festung", "firma",
    "flugplatz", "forschung", "freiheit", "freiwillig",
    "gasse", "gebirge", "gefahr", "gemeinschaft", "generator",
    "geschwindigkeit", "gewitter", "glocke", "gold",
    "graben", "grundstueck", "guertel",
    "haehne", "halde", "handwerk", "hauptquartier",
    "haustier", "horizont", "huegel",
    "industrie", "initiative",
    "kampf", "kaserne", "kette", "kloster", "komet",
    "kontakt", "koordinaten", "kraft", "kreuzung",
    "lampe", "landschaft", "lawine", "leuchtturm", "loewe",
    "meer", "meister", "mitternacht", "mittag",
    "nebel", "ordnung",
    "parade", "patrouille", "perimeter", "phantom", "pirat",
    "radar", "rampe", "rauch", "reserve", "richtung",
    "ritter", "rotation",
    "satellit", "schatten", "schicht", "schloss", "schluessel",
    "schneise", "schussel", "schwert", "sektor", "silber",
    "spur", "stern", "stille", "strasse",
    "teleskop", "truemmer",
    "uebung", "uhr",
    "verbindung", "versorgung", "vorposten",
    "waechter", "welle", "werkstatt", "wolke",
    "zaun", "ziel", "zuflucht",
]

# Common English words used as channel names
ENGLISH_WORDS = [
    "admin", "alert", "backup", "bot", "broadcast",
    "channel", "chat", "cluster", "command", "control",
    "data", "debug", "default", "demo", "dev", "device",
    "edge", "emergency", "event", "exchange",
    "firmware", "flash", "forward",
    "general", "global", "grid", "group",
    "hack", "hacker", "hello", "help", "home", "host", "hub",
    "iot", "irc",
    "key",
    "lab", "link", "live", "local", "log", "login",
    "main", "maker", "makerspace", "map", "master", "message",
    "mobile", "monitor", "mqtt",
    "net", "network", "news", "notify",
    "offline", "open", "ops", "outdoor",
    "ping", "pong", "portal", "power", "private", "public",
    "radio", "range", "raw", "remote", "root", "rover",
    "scan", "secret", "server", "setup", "signal", "smart",
    "status", "stream", "switch", "sync", "system",
    "team", "telemetry", "terminal", "testing", "tool", "tower",
    "update", "user",
    "village", "voice",
    "warning", "watch", "weather", "wifi", "wireless", "world",
    "zone",
    # More
    "alpha", "beta", "gamma", "delta", "epsilon", "omega",
    "north", "south", "east", "west",
    "fire", "water", "earth", "air", "storm",
    "shadow", "ghost", "phantom", "stealth", "silent",
    "eagle", "falcon", "hawk", "wolf", "bear", "fox",
    "thunder", "lightning", "flash", "bolt",
    "base", "camp", "fort", "outpost", "bunker",
    "patrol", "scout", "sniper", "ranger", "guard",
    "crypto", "cipher", "code", "decode", "encrypt",
    "matrix", "nexus", "oracle", "prism", "vortex",
    "apex", "core", "pulse", "wave", "flux",
    "dark", "light", "night", "dawn", "dusk",
    "rescue", "safety", "shield", "armor", "sentinel",
    "relay", "beacon", "tower", "antenna", "ground",
    "solar", "battery", "offgrid", "preppers", "survive",
]

# Hackerspaces, makerspaces, community groups — complete DACH + NL/BE list
# Source: wiki.hackerspaces.org (Feb 2026)
HACKERSPACES = [
    # Generic terms
    "c3", "ccc", "cccamp", "chaos", "chaostreff", "congress",
    "camp", "ctf", "fablab", "hackerspace", "hackspace",
    "makerspace", "metalab", "shackspace", "maker", "hacker",
    # CCC congresses + camps
    "32c3", "33c3", "34c3", "35c3", "36c3", "37c3", "38c3", "39c3",
    "cccamp23", "cccamp19", "cccamp15", "mrmcd", "gulasch",
    "gpn", "gpn22", "gpn23", "easterhegg", "eh20", "eh21",
    # Germany — Hackerspaces (wiki.hackerspaces.org/Germany)
    "attraktor", "c-base", "cbase", "c4", "chaosdorf", "chaospott",
    "entropia", "dezentrale", "flipdot", "backspace", "nerdberg",
    "stratum0", "stratum", "shackspace", "raumzeitlabor", "raumzeit",
    "binary", "binarykitchen", "xhain", "dingfabrik", "das-labor",
    "daslabor", "garagelab", "hackzogtum", "leinelab", "warpzone",
    "maschinendeck", "krautspace", "bytespeicher", "werkraum",
    "maschinenraum", "rabbithole", "c3d2", "muccc", "cccffm",
    "nerd2nerd", "hackwerk", "essembly", "section77", "freieslabor",
    "acmelabs", "acme", "datenburg", "hackhro", "nobreakspace",
    "temporaerhaus", "reactos", "c3re", "hackzogtum", "hacklabor",
    "schaffenburg", "haxko", "leitstelle511", "z-labor", "kanthaus",
    "devtal", "spacedotbi", "hacknology", "geolab", "hacksaar",
    "bitsnbugs", "netz39", "un-hack-bar", "duisentrieb",
    "openlab", "openlabaugsburg", "macherburg", "reaktor23",
    "raumfahrtagentur", "k4cg", "c-hack", "hopfenspace",
    "bytewerk", "westwoodlabs", "chaosinkel", "mithackzentrale",
    "eigenbaukombinat", "machwerk", "freiraum", "motionlab",
    "fnordeingang", "metameute", "namespace", "in-berlin",
    "bastelbude", "munichmakerlab", "freespace", "entitaet",
    "nerdbridge", "infuanfu", "seebase", "zam", "nordlab",
    "labor23", "magrathea", "localhorst", "wurmloch",
    "imaginaerraum", "milliways", "hecospace", "hackbar",
    "audiolab", "straze", "proto-lab", "suma", "codeforheilbronn",
    "fem", "spline", "hasi", "kollab", "lug", "belug",
    "toppoint", "comakingspace", "digitac", "happylab",
    "technik-cafe", "technikcafe", "planraum",
    "cccbremen", "ccchamburg", "cccmainz", "cccdarmstadt",
    "cccmannheim", "cccfreiburg", "cccgoettingen", "cccberlin",
    "cccaachen", "cccac",
    "fablabnuernberg", "fablabmunich", "faufablab",
    "fablabcottbus", "fablablueneburg", "fablabwuerzburg",
    "fablabheidelberg", "fablabaachen", "fablabneuen",
    "makerspacebonn", "makerspaceminden", "makerspaceleipzig",
    "makerspacewacken", "zwonulleins",
    # Austria (wiki.hackerspaces.org/Austria)
    "metalab", "realraum", "it-syndikat", "itsyndikat",
    "devlol", "cccsalzburg", "segmentationvault", "usr-space",
    "chaosklaus", "openlandlab", "otelo", "makeraustria",
    "machquadrat", "chaoscafe", "mutterschiff", "pannonian",
    # Switzerland (wiki.hackerspaces.org/Switzerland)
    "ccczh", "coredump", "mechartlab", "codinglab",
    "chaostreffbern", "cccbasel", "starshipfactory",
    "posttenebraslab", "fixme", "ruum42", "engrenage",
    "hackuarium", "gaudilabs", "dock18", "laborluzern",
    # Netherlands (wiki.hackerspaces.org/Netherlands)
    "bitlair", "pixelbar", "tkkrlab", "hack42", "randomdata",
    "maakplek", "nurdspace", "hackalot", "technologia",
    "ackspace", "tdvenlo", "revelationspace",
    "makersbase", "hive5", "mononoke",
    # Belgium + Luxembourg
    "hsbxl", "syn2cat", "chaosstuff", "whitespace",
    "voidwarranties", "wolfplex",
    # Freifunk communities
    "freifunk", "ffbs", "ffh", "ffhh", "ffnord", "ffrl",
    "ffmuc", "ffda", "ffrgb", "ffwi", "ffof", "ffho",
    "ffgt", "fffl", "ffka", "ffhb", "ffol", "ffnw",
    "ffac", "ffbi", "ffbn", "ffdo", "ffgoe", "ffhef",
    "ffhl", "ffki", "ffkbu", "ffmr", "ffms", "ffnbg",
    "ffog", "ffpb", "ffpi", "ffsl", "fftrier", "ffwaf",
    # Generic hacker/maker terms
    "hackathon", "hackday", "barcamp", "nerdcave", "bytewelt",
    "werkstatt", "devhaus", "eigenlab", "noname",
    "segfault", "kernel", "rootkit", "overflow",
    "nullbyte", "blackhat", "whitehat", "defcon",
    "lockpick", "soldering", "arduino", "raspi",
]

# Emergency / BOS / prepper
EMERGENCY = [
    "bos", "blaulicht", "brand", "dlrg", "drk", "einsatz",
    "feuerwehr", "katastrophe", "katschutz", "krisenvorsorge",
    "malteser", "polizei", "rettung", "rettungsdienst",
    "sanitaeter", "sirene", "sos", "thw", "warnung",
    "zivilschutz", "notstrom", "notfunk", "katastrophenschutz",
    "blackout", "stromausfall", "hochwasser", "unwetter",
    "erdbeben", "tsunami", "alarm", "evakuierung", "sammelplatz",
    "ersthelfer", "sanitaet", "bergrettung", "wasserrettung",
    "flugrettung", "seenotrettung", "seenotruf", "mayday",
    "panpan", "securite", "epirb", "plb",
    "prepper", "preppergruppe", "bugin", "bugout",
    "survival", "bushcraft", "wildnis", "autark",
]

# NATO phonetic + greetings + memes
MISC_WORDS = [
    # NATO phonetic
    "alfa", "bravo", "charlie", "delta", "echo", "foxtrot",
    "golf", "hotel", "india", "juliet", "kilo", "lima",
    "mike", "november", "oscar", "papa", "quebec", "romeo",
    "sierra", "tango", "uniform", "victor", "whiskey",
    "xray", "yankee", "zulu",
    # German greetings
    "hallo", "moin", "moinmoin", "servus", "gruezi",
    "tschuess", "gutenmorgen", "gutentag", "gutenabend",
    "mahlzeit", "prost", "cheers", "ahoi",
    # Internet/meme
    "lol", "omg", "wtf", "yolo", "geil",
    "cool", "nice", "super", "mega", "ultra",
    "foo", "bar", "baz", "asdf", "qwerty", "test123",
    "abc", "xyz", "aaa", "zzz", "xxx",
    # Directions
    "nord", "sued", "ost", "west",
    "nordost", "nordwest", "suedost", "suedwest",
    "oben", "unten", "links", "rechts",
    # German numbers
    "eins", "zwei", "drei", "vier", "fuenf",
    "sechs", "sieben", "acht", "neun", "zehn",
    "elf", "zwoelf", "hundert", "tausend",
    # Math/science
    "pi", "tau", "phi", "null",
    # Short
    "ja", "nein", "ok", "ack", "nack",
    "on", "off", "go", "stop", "start", "run",
    "tx", "rx", "io",
    "v1", "v2", "v3", "v4", "v5",
    "wx", "dx",
    # Seasonal
    "advent", "fasching", "karneval", "weihnachten", "ostern",
    "silvester", "oktoberfest", "schuetzenfest", "kirmes",
    "volksfest", "pfingsten", "sommer", "herbst", "fruehling",
    # Single letters
    "a", "b", "c", "d", "e", "f", "g", "h", "i", "j",
    "k", "l", "m", "n", "o", "p", "q", "r", "s", "t",
    "u", "v", "w", "x", "y", "z",
    # Empty (default public channel)
    "",
    # Previously cracked by brute-forcer
    "jjvba6", "6v877", "aqou1w", "j90w5y", "c38y00", "t2395f",
]

# Known MeshCore community channel names
KNOWN_COMMUNITY = [
    # ===== Official MeshCore community channels (from meshcorenetz.de, hansemesh.de, meshcore.ch) =====
    # These are KNOWN active channels used by German/DACH mesh communities
    "hansemesh", "bsmesh", "meshcorenetz", "dl-mitte",
    "funkfeuer", "voyagermesh", "blackforestmesh",

    # Swiss channels (meshcore.ch/channels)
    "switzerland", "svizzera", "suisse", "chtest",
    "zuerich", "wallis", "bern", "seeland",
    "hamradio", "qrv", "netzstatus",
    "flachwitze", "warnings", "blackout", "hard-software",
    "austria", "aurora-alert", "italia",

    # Regional mesh community names
    "wicmp", "bsmeshde", "region38", "region38mesh",
    "bs38", "mesh38", "himesh", "funkberg",
    "hildesheim-mesh", "hannover-lora", "bs-lora",
    "harzmesh", "harzlora", "elmfunk",
    "deisterfunk", "deistermesh",
    "msh3", "voyager", "meshbremen", "bremenmesh",
    "meshrheinlandpfalz", "meshkarlsruhe", "meshtaunus",
    "meshhungary", "meshuk", "meshfrance",
    "bs-mesh", "wob-mesh", "pe-mesh", "gs-mesh", "gf-mesh",
    "harz-mesh", "lora-bs", "lora-wob", "lora-harz",
    "meshbs", "meshwob", "meshgf", "meshpe",

    # City+mesh patterns (all major German cities)
    "hannover-mesh", "hamburg-mesh", "berlin-mesh", "koeln-mesh",
    "muenchen-mesh", "frankfurt-mesh", "dresden-mesh", "leipzig-mesh",
    "freiburg-mesh", "karlsruhe-mesh", "nuernberg-mesh", "stuttgart-mesh",
    "mesh-hannover", "mesh-hamburg", "mesh-berlin", "mesh-koeln",
    "mesh-freiburg", "mesh-karlsruhe", "mesh-nuernberg", "mesh-stuttgart",
    "mesh-bremen", "mesh-kiel", "mesh-rostock", "mesh-erfurt",
    "mesh-magdeburg", "mesh-dortmund", "mesh-essen", "mesh-duesseldorf",
    "lora-hannover", "lora-hamburg", "lora-berlin", "lora-koeln",
    "lora-freiburg", "lora-karlsruhe", "lora-nuernberg", "lora-stuttgart",
    "meshhannover", "meshhamburg", "meshberlin", "meshkoeln",
    "meshmuenchen", "meshfrankfurt", "meshdresden", "meshleipzig",
    "meshkiel", "meshrostock", "mesherfurt", "meshmagdeburg",
    "meshstuttgart", "meshdortmund", "meshessen", "meshduesseldorf",
    "meshduisburg", "meshbochum", "meshwuppertal",
    "meshaachen", "meshfreiburg", "meshnuernberg", "meshaugsburg",
    "meshbraunschweig", "meshwolfsburg", "meshgoslar", "meshhildesheim",

    # Known MeshCore repeater/room naming patterns
    "meshcore", "meshcorebot", "letsmesh", "meshtastic",
    "repeater1", "repeater2", "relay1", "relay2",
    "node01", "node02", "node1", "node2",
    "basis", "basisstation", "zentrale",
    "hauptkanal", "nebenkanal", "backup",

    # Common test/setup channels
    "meinkanal", "meinmesh", "meinnetz", "meintest",
    "testkanal", "testnetz", "testmesh", "testchannel",
    "erstertest", "helloworld", "hellomesh", "hellolora",
    "testtest", "kanaltest", "meshtest", "loratest",

    # Fun/social channels
    "lorabande", "meshbande", "funkergruppe",
    "nachtfunker", "fruehfunker", "meshpiraten",
    "funktrupp", "lichtfunk", "stille",
    "kaffeefunk", "bierfunk", "feierabendfunk",
    "sonntagsrunde", "samstagsrunde",
    "meshlocal", "meshglobal", "meshworld",
    "longrange", "longrangetest", "rangetest",
    "dxtest", "reichweite", "entfernung",
    "offgrid", "solarmesh", "solarnode", "batterynode",
    "portabel", "portable", "unterwegs", "onthego",
    "daheim", "zuhause", "athome",
    "dachboden", "keller", "garage", "werkstatt",
    "gartenmesh", "gartenhaus", "laube", "schuppen",
    "gartenstadt",

    # AT/CH mesh
    "oe-mesh", "hb9-mesh", "alpenmesh", "bergfunk",
    "meshwien", "meshgraz", "meshlinz", "meshsalzburg",
    "meshinnsbruck", "meshzuerich", "meshbern", "meshbasel",
    # NL mesh
    "meshamsterdam", "meshrotterdam", "meshutrecht",
    "mesheindhoven", "meshgroningen",
    # General patterns
    "meshde", "lorade", "mesheu", "loraeu",
    "mesh-de", "lora-de", "mesh-eu", "lora-eu",
    "meshnet", "loranet", "meshlan", "loralan",

    # Braunschweig specifics
    "okertal", "okertalsperre", "okerstausee",
    "ringgleis", "burgplatz", "kohlmarkt",
    "hagenmarkt", "magniviertel",
]


def build_extended_wordlist() -> set[str]:
    """Build the complete wordlist from all sources + generators."""
    words: set[str] = set()

    # Static lists
    for source in [GERMAN_CITIES, AUSTRIAN_CITIES, SWISS_CITIES,
                   DUTCH_BELGIAN_CITIES, GEOGRAPHY, HAM_RADIO,
                   MESH_LORA, GERMAN_WORDS, ENGLISH_WORDS,
                   HACKERSPACES, EMERGENCY, MISC_WORDS,
                   KNOWN_COMMUNITY]:
        words.update(source)

    # Generated: all German PLZ
    words.update(_generate_plz())

    # Generated: mesh patterns (mesh1, mesh-bs, etc.)
    words.update(_generate_mesh_patterns())

    # Generated: number combos (0-9999)
    words.update(_generate_number_combos())

    # All DACH callsigns (~3.6M entries, ~2-3s build time)
    words.update(_generate_callsign_patterns())

    return words


# Pre-build the wordlist at import time
EXTENDED_WORDLIST = build_extended_wordlist()
