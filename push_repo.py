import os, sys, base64, pathlib, requests
tok=os.getenv("GITHUB_TOKEN"); user=os.getenv("GITHUB_USER"); repo=os.getenv("REPO_NAME")
if not tok or not user or not repo: print("Missing envs"); sys.exit(1)
API=f"https://api.github.com/repos/{user}/{repo}/contents"
HEAD={"Authorization":f"Bearer {tok}","Accept":"application/vnd.github+json"}

def get_sha(p):
    r=requests.get(f"{API}/{p}", headers=HEAD)
    return r.json().get("sha") if r.status_code==200 else None

def put(p, data):
    body={"message":f"Add/update {p}","content":base64.b64encode(data).decode()}
    sha=get_sha(p)
    if sha: body["sha"]=sha
    r=requests.put(f"{API}/{p}", headers=HEAD, json=body)
    print(("Uploaded" if r.status_code in (200,201) else f"FAIL {r.status_code}"), p, r.text[:120])

root=pathlib.Path(".").resolve()
skip={".git","__pycache__","venv",".venv",".vscode",".idea"}
for path in root.rglob("*"):
    if path.is_dir() or any(seg in skip for seg in path.parts): continue
    with open(path,"rb") as f: put(str(path.relative_to(root)).replace("\\","/"), f.read())
print(f"Done → https://github.com/{user}/{repo}")
