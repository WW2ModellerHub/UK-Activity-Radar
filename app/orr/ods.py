import sys, zipfile, xml.etree.ElementTree as ET
T="urn:oasis:names:tc:opendocument:xmlns:table:1.0"; X="urn:oasis:names:tc:opendocument:xmlns:text:1.0"; O="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
def sheets(path):
    yield from _sheets(zipfile.ZipFile(path).read("content.xml"))

def sheets_from_bytes(data):
    import io
    yield from _sheets(zipfile.ZipFile(io.BytesIO(data)).read("content.xml"))

def _sheets(content):
    root=ET.fromstring(content)
    for t in root.iter(f"{{{T}}}table"):
        rows=[]
        for r in t.iter(f"{{{T}}}table-row"):
            rep=int(r.get(f"{{{T}}}number-rows-repeated","1"))
            cells=[]
            for c in r:
                n=int(c.get(f"{{{T}}}number-columns-repeated","1"))
                v=c.get(f"{{{O}}}value") or "".join("".join(p.itertext()) for p in c.iter(f"{{{X}}}p"))
                cells += [v]*min(n,60)
            while cells and cells[-1]=="": cells.pop()
            if cells: rows += [cells]*min(rep,1)
        yield t.get(f"{{{T}}}name"), rows
if __name__=="__main__":
    for name,rows in sheets(sys.argv[1]):
        print("==",name,len(rows))
        for r in rows[:8]: print(r[:14])
