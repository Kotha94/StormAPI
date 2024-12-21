import os
import json
import time
from threading import Thread
from datetime import datetime, timedelta
from pymongo import MongoClient
import websocket
import requests
import xml.etree.ElementTree as Tree
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Fetch variables from environment
GAME_USERNAME = os.getenv("GAME_USERNAME")
GAME_PASSWORD = os.getenv("GAME_PASSWORD")
MONGO_URI = os.getenv("MONGO_URI")

class MySocket(websocket.WebSocketApp):
    def __init__(self, url, serveur_header, royaume, nom, mdp, intervalle):
        super().__init__(url, on_open=self.on_open, on_message=self.on_message, on_error=self.on_error, on_close=self.on_close)
        self.serveur_header = serveur_header
        self.royaume = royaume
        self.nom = nom
        self.mdp = mdp
        self.intervalle = intervalle
        self.fortos = []
        self.next_scan = ""
        self.last_x = -1
        self.last_y = -1
        self.last_request = -1
        self.nb_fail = 0
        
        self.mongo_client = MongoClient(MONGO_URI)
        self.db = self.mongo_client["GameData"]
        self.forts_collection = self.db["Forts"]

    def on_open(self, ws):
        print("### socket connected ###")
        time.sleep(1)
        self.send(f"""<msg t='sys'><body action='login' r='0'><login z='{self.serveur_header}'><nick><![CDATA[]]></nick><pword><![CDATA[1065004%fr%0]]></pword></login></body></msg>""")
        self.send(f"""%xt%{self.serveur_header}%lli%1%{{"CONM":175,"RTM":24,"ID":0,"PL":1,"NOM":"{self.nom}","PW":"{self.mdp}","LT":null,"LANG":"fr","DID":"0","AID":"1674256959939529708","KID":"","REF":"https://empire.goodgamestudios.com","GCI":"","SID":9,"PLFID":1}}%""")

    def run(self):
        while True:
            self.start_scan_map()
            self.next_scan = (datetime.now() + timedelta(seconds=self.intervalle * 60)).strftime('%H:%M:%S')
            for i in range(self.intervalle):
                self.send(f"""%xt%{self.serveur_header}%pin%1%<RoundHouseKick>%""")
                for j in range(6):
                    if self.last_request != -1 and time.time() - self.last_request > 10:
                        self.nb_fail += 1
                        if self.nb_fail >= 3:
                            self.scan_map_cells(self.last_x // 13 + self.last_y // 1170, (self.last_y // 13 + 10) % 100)
                        else:
                            self.scan_map_cells(self.last_x, self.last_y)
                    time.sleep(10)

    def start_scan_map(self):
        print("Scan de la carte en cours : 0%")
        self.scan_map_cells(0, 0)

    def scan_map_cells(self, x, y):
        self.last_x = x
        self.last_y = y
        self.last_request = int(time.time())
        self.nb_fail = 0
        try:
            for i in range(10 - y // 90):
                self.send(f"""%xt%{self.serveur_header}%gaa%1%{{"KID":{self.royaume},"AX1":{13 * x},"AY1":{13 * (y + i)},"AX2":{13 * x + 12},"AY2":{13 * (y + i) + 12}}}%""")
        except websocket.WebSocketConnectionClosedException as e:
            print("Connexion perdue. Reconnexion en cours...")
            self.on_close(None, None, None)
            raise e

    def finish_scan_map(self):
        print("Scan de la carte en cours : 100%")
        self.last_x = -1
        self.last_y = -1
        self.last_request = -1
        self.nb_fail = 0

        fort_documents = [
            {
                "CoordX": fort[0],
                "CoordY": fort[1],
                "Level": fort[2],
                "Difficulty": fort[3],
                "AttacksLeft": fort[4],
                "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            for fort in self.fortos
        ]

        try:
            self.forts_collection.delete_many({})

            if fort_documents:
                result = self.forts_collection.insert_many(fort_documents)
                print(f"{len(result.inserted_ids)} forts insérés dans la base de données.")
                print(f"{len(result.inserted_ids)} forts insérés à {datetime.now().strftime('%H:%M:%S')}. Prochain scan à {self.next_scan}.")
            else:
                print(f"Aucun fort trouvé. Prochain scan à {self.next_scan}.")
        except Exception as e:
            print("Erreur lors de l'insertion dans MongoDB :", e)

        self.fortos.clear()

    def on_message(self, ws, message):
        message = message.decode('UTF-8')
        if message[:12] == "%xt%lli%1%0%":
            Thread(target=self.run, daemon=True).start()
        elif message[:10] == "%xt%lli%1%" and message[10] != "0":
            print("La connexion au serveur a échoué. Vérifiez que le nom d'utilisateur et le mot de passe sont corrects et que vous avez sélectionné le bon serveur.")
            self.close()
        elif message[:12] == "%xt%gaa%1%0%":
            data = json.loads(message[12:-1])
            for castle in data["AI"]:
                if (len(castle) == 9) and (castle[0] == 25):
                    if (castle[-1] == 0) and (castle[-3] == 0) :
                        if castle[5] == 9:
                            self.fortos.append([castle[1], castle[2], 80, 0, 10 - castle[-2]])
                        elif castle[5] == 8:
                            self.fortos.append([castle[1], castle[2], 70, 0, 10 - castle[-2]])
                        elif castle[5] == 14:
                            self.fortos.append([castle[1], castle[2], 80, 1, 10 - castle[-2]])
                        elif castle[5] == 13:
                            self.fortos.append([castle[1], castle[2], 70, 1, 10 - castle[-2]])
            if data["AI"][0][2] // 13 == 98:
                if data["AI"][0][1] // 13 == 98:
                    self.finish_scan_map()
            if (data["AI"][0][2] // 13) % 10 == 0 and (data["AI"][0][1] // 13 != 98 or data["AI"][0][2] // 13 != 90):
                self.scan_map_cells(data["AI"][0][1] // 13 + data["AI"][0][2] // 1170, (data["AI"][0][2] // 13 + 10) % 100)
        elif message[:10] == "%xt%gaa%1%" and message[10] != "0":
            print("Ce compte n'a pas de chateau dans ce royaume.")
            self.close()

    def on_error(self, ws, error):
        print("### Error ###")
        print(error)

    def on_close(self, ws, close_status_code, close_msg):
        print("### Socket disconnected ###")

def main():
    SERVER = "International 2"
    ROYAUME = 4
    SCAN_INTERVAL = 5
    URL = "wss://ep-live-mz-int2-es1-it1-game.goodgamestudios.com"
    SERVEUR_HEADER = "EmpireEx_7"
    # Create a websocket application
    ws_app = MySocket(URL, SERVEUR_HEADER, ROYAUME, GAME_USERNAME, GAME_PASSWORD, SCAN_INTERVAL)
    ws_app.run_forever()

if __name__ == "__main__":
    main()
