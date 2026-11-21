import sys,re,zlib,struct
def nib(b): return bytes(((x<<4)&0xF0)|(x>>4) for x in b)
d=open(sys.argv[1],'rb').read()
pos=0;MK=b'\x14\x00\x06\x00\x08\x00'
for m in re.finditer(re.escape(MK),d):
    i=m.start()
    a,b,c,e,n=struct.unpack_from('<IIIII',d,i+6)
    name=nib(d[i+26:i+26+n])
    data=d[i+26+n:i+26+n+c]
    try: u=zlib.decompress(data,-15); ok=len(u)
    except Exception as ex: u=b'';ok='ERR'
    print(i,hex(a),b,c,e,name,ok, u[:80] if b'xml' in u[:100] or b'<' in u[:5] else '')
