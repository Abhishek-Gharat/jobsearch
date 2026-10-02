import urllib.request
import json
import re

redirect_urls = [
    "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQFAy4QZHv13rJrhvkLRRbtPsNansQKhwVRc-GQgAgKm1PH3pSXtxPjoB_4f0I2WvBc3YZUxjicKH0S-2B99nrDGWlQdyq_3x3xx-MFhBRQ2b6T4NBGSbLrya6D86DmbPLdJK8fHiDTT4R0=",
    "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQG5oidgu9-ePDTXJRlJ0s7Cp5COsl2omBsZO1vJ-E1hVLvr_LiRtlpV2HXuD3BtaXdEMHrvEHZpdlfxaCCt5qcfl0zvkLf9s3Z84R5Hc9xu8W12eXtS32e3XVouiIIIorihFE3iolYojsjJpzNqdA==",
    "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQFKqsgUUredvRGjJ8JDIRrW3Nhp92L4VoNeHipVz1AxWytEsXpza_deDCiBzwUW96hZCwkIkNpPsM5nNld4_ow4SV7Zla6zKcOsgZbf8FWowDdcpWqGoIJBDtNEteOib7ZbYmGcsYApuETYNJi9Q4ANkVl-6GM=",
    "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQGKdVoC-rCuZ1ugVmIri545wxI4xVYNiKRKWAMHwEop24ZPnVAxmKmMD4pHZs4t4hwAoIclL2kUBkrZinZ4Zk70vasdRhXYYfp1Z0Y_aHs-9TM1S4uZoGMVcFh2ZJ6RNqcU-5vWo_Owb50M5S0L0TjaVw==",
    "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQH1Lod8_z0Zc1hq5Bcs_o8nOKV2JYD9C6O7P8oi2V2BWjkW8Oe09zPV48KaRITzHBDG5ChOzdKe_xuk1Qtngdwoif5FvSebLxwOcZTTaHiObmWaB-yHiHFTuYdsOMrkzq5dgq_gx9KLmnV9",
    "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQEDIURRAd0uri2rXKXwMIxY2cS9jcbMOD25-w6ar1mBRoYjDgRIJGFVIFE7djEwaYUdBtA3ZtcslgpUjtLC3aLBhb6f5hc7aH2ouS_b3lv_PSqRSQIp5K6or12YWoiD_wdGpTA5zqqsLVUsjzF-7tvWYBcDN1o_X-QP",
    "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQEAeXXW4ToXIOHXajdj5lVYf8ooDG1wRGBvog2zh2O8UuEZsaw2ji4dQgsHyqhP0m2TQznVxa3aSuQm9xeEBY4Yy9PVQOLu7tbSPhUKDm1hf8AQZXHYeNJkmq2CGvY_bCcF7XnZTCKIXzV1dFmAwSjKObul09Ieg3_eKSzs",
    "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQEcKFpb-zBpXwP0X2V3DxEUboIHlOP2qSBbGS_DVwmTbg3mX9Urt13cUq4GmSh1etUGUDM2_Nytl9B_A7ATwylKgiiMBqrboRTNVTu8EZEsHNrMljM3cjP7FNjbFPz-1c1J5qvO649R9ehmBXOKaKuzzUc="
]

final_urls = []
for u in redirect_urls:
    try:
        req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            final_urls.append(resp.geturl())
    except Exception as e:
        print(f"Error {u}: {e}")

for f in final_urls:
    print("Resolved:", f)
