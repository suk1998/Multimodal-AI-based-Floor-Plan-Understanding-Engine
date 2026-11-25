"""Initial extensible space lexicon shared by OWL export and Spatial analysis.

It proposes name/type interpretations, never room geometry or required rooms.
"""
import hashlib,json,re,unicodedata

BASE_SPACE_TERMS = (
    ("침실1", "Bedroom", 1, None),
    ("침실2", "Bedroom", 2, None),
    ("침실3", "Bedroom", 3, None),
    ("거실", "LivingRoom", None, None),
    ("주방/식당", "KitchenDining", None, None),
    ("현관", "Entrance", None, None),
    ("드레스룸", "DressingRoom", None, None),
    ("실외기실", "OutdoorUnitRoom", None, None),
    ("다용도실", "UtilityRoom", None, None),
    ("가족욕실", "Bathroom", None, "Family"),
    ("부부욕실", "Bathroom", None, "Primary"),
    ("발코니1", "Balcony", 1, None),
    ("하향식피난구", "EvacuationArea", None, None),
    ("팬트리", "Pantry", None, None),
)


def normalize_label(raw):
    value=''.join(unicodedata.normalize('NFKC',str(raw)).split())
    if value in ('주방식당','주방·식당','주방ㆍ식당'):value='주방/식당'
    return value


def resolve_space_term(raw,terms=BASE_SPACE_TERMS):
    value=normalize_label(raw)
    return next((term for term in terms if normalize_label(term[0])==value),None)


def vocabulary_digest():
    return hashlib.sha256(json.dumps(BASE_SPACE_TERMS,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


# Shared user-provided opening glossary. schema.py re-exports this unchanged.
BASE_ELEMENT_CODES = (
    ("WD/1", "Door", "Wood Door", "Wood", None),
    ("WD/2", "Door", "Wood Door", "Wood", None),
    ("FSD/1", "FireDoor", "Fire Steel Door", "Steel", "FireProtection"),
    ("AG/1", "Grille", "Aluminum Grille", "Aluminum", "Ventilation"),
    ("SD/1", "Door", "Steel Door", "Steel", None),
    ("PD/1", "Door", "Plastic Door", "Plastic", None),
    ("PD/2", "Door", "Plastic Door", "Plastic", None),
    ("PD/3", "Door", "Plastic Door", "Plastic", None),
    ("AW/1", "Window", "Aluminum Window", "Aluminum", None),
    ("PW/1", "Window", "Plastic Window", "Plastic", None),
    ("PW/2", "Window", "Plastic Window", "Plastic", None),
    ("PW/3", "Window", "Plastic Window", "Plastic", None),
    ("PW/4", "Window", "Plastic Window", "Plastic", None),
    ("PW/5", "Window", "Plastic Window", "Plastic", None),
)
