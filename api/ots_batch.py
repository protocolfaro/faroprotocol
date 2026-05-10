import hashlib; def procesar(lista): [print(hashlib.sha256(d.encode()).hexdigest()) for d in lista]
