CALIBRATION_STEPS = (
    {
        "key": "sitting",
        "label": "正常坐姿",
        "instruction": "请孩子自然坐好，双肩放松，身体面向书桌。",
        "speech": "请保持平时舒服的坐姿，看向前方。",
    },
    {
        "key": "writing",
        "label": "正常写字",
        "instruction": (
            "请孩子按平时习惯写几行字，保持自然，不用刻意挺直。"
        ),
        "speech": "请像平时一样写几行字。",
    },
    {
        "key": "reading",
        "label": "正常阅读",
        "instruction": "请孩子自然阅读桌上的书本，保持常用的阅读距离。",
        "speech": "请像平时一样阅读桌上的书。",
    },
    {
        "key": "lighting",
        "label": "正常光线",
        "instruction": (
            "请把书桌灯光调到家长认可的亮度，孩子保持在书桌前。"
        ),
        "speech": "请把灯光调到合适亮度，并保持在书桌前。",
    },
)

CALIBRATION_STEP_KEYS = tuple(item["key"] for item in CALIBRATION_STEPS)
CALIBRATION_STEP_BY_KEY = {item["key"]: item for item in CALIBRATION_STEPS}
