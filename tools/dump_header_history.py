from app.utils.polygon_client import _header_history
print('history len=', len(_header_history))
for h in list(_header_history):
    print(h)
