import json, time
import requests

# les 2 liens donnes par le prof (prix du marche + mon portefeuille)
MARKET_URL = "https://raw.githubusercontent.com/exofri/ensim-trade-center/refs/heads/main/docs/data/market.json"
HOLDINGS_URL = "https://raw.githubusercontent.com/exofri/ensim-trade-center/refs/heads/main/traders/gaspard/holdings.json"

# classement du prof (valeur totale de chaque trader, dont les 3 baselines)
LEADERBOARD_URL = "https://raw.githubusercontent.com/exofri/ensim-trade-center/refs/heads/main/docs/data/leaderboard.json"
# notre journal, lu par le dashboard (docs/index.html)
JOURNAL = "docs/data/journal.json"

MIN_MONEY_RESERVE = 50 # on garde toujours au moins 50 d'argent, on n'achete pas en dessous

def fetch_json(url):
    resp = requests.get(url, proxies={"http": None, "https": None}, timeout=(10, 20))
    resp.raise_for_status()
    return resp.json()

# en regardant les courbes de prix :
# - A monte et descend en cycle (environ 48 ticks entre 2 sommets)
# - C fait pareil mais plus vite (environ 24 ticks)
# - B fait un cycle beaucoup plus long (environ 96 ticks) avec une tendance qui monte
#   (au debut je pensais qu'il bougeait au hasard, le cycle se voit mieux en prenant du recul)
# donc pour les 3 produits on fait du retour a la moyenne (bandes de Bollinger) :
# prix au dessus de la moyenne -> il va redescendre -> on vend
# prix en dessous de la moyenne -> il va remonter -> on achete
# fenetres choisies en testant sur les ticks 0-200, verifiees sur 200-343
#
# B (change au tick 375) : avec une fenetre de 50 (un demi-cycle) la moyenne suivait
# le cycle lui-meme, z passait au dessus de 0.5 des le milieu de la montee et on a
# tout vendu (0 unite de B des le tick 368) alors que B continuait a monter.
# Maintenant :
# - fenetre de 96 = un cycle complet : la moyenne lisse le cycle et ne garde que la
#   tendance qui monte, donc z dit vraiment ou on en est dans le cycle
# - on ne vend que pres du pic (z > 1) et jamais en dessous d'un plancher de 400 unites :
#   on vend environ 60 % du stock au pic et on garde le reste qui profite de la tendance
# backtest sur l'historique (budget 3000 pour B seul) : meilleur que "ne rien faire" a
# chaque pic et chaque creux depuis le tick 192 ; resultats proches pour une fenetre
# de 80 a 96, moins bons au dela de 104
#
# C (change au tick 375) : sinus qui baisse (pic 3.58 au tick 19, 1.48 au tick 353)
# mais qui prend environ +50 % du creux au pic. Les unites gardees d'un pic au suivant
# perdent de la valeur, donc on veut 0 unite au pic et le maximum au creux.
# On ne peut echanger que 100 unites par tick et une montee dure ~12 ticks, donc on
# vise un stock max de 1200 qu'on peut vider pendant la montee :
# - z > 0 (partie haute du cycle) : on vend tout ce qu'on a, 100 par tick
#   backtest sur la verification selon le seuil de vente : z > 0 -> +2400,
#   z > 0.25 -> +2200, z > 0.5 -> +2085. Plus on attend, plus on arrive au pic avec
#   des unites en main, donc on vend des que z > 0
# - z < -0.5 (pres du creux) : on rachete jusqu'a 1200 unites
# backtest (regle sur 100-250, verifie sur 250-374) : +2400 contre +2160 pour l'ancienne
# regle sur la verification ; resultats proches pour une fenetre de 16 a 20 et un
# stock max de 1000 a 1500
fenetre = {"A": 48, "B": 96, "C": 20}
seuil = 0.5   # si le prix est trop proche de la moyenne on ne fait rien (bruit)
seuil_vente = {"B": 1.0, "C": 0.0}   # B : on attend d'etre pres du pic ; C : voir plus haut
plancher = {"B": 400}      # unites de B qu'on garde toujours (on ne vend pas en dessous)
stock_max = {"C": 1200}    # C : on vend tout si z > 0, on remonte jusqu'a ce stock au creux


def calcul_z(prices, product):
    # z = de combien d'ecarts-types le prix actuel est loin de la moyenne
    # renvoie None si on ne peut pas le calculer (B, ou pas assez d'historique)
    if product not in fenetre:
        return None
    n = fenetre[product]
    if len(prices) < n:
        return None
    derniers = prices[-n:]
    moyenne = sum(derniers) / n
    ecart_type = (sum((x - moyenne) ** 2 for x in derniers) / n) ** 0.5
    if ecart_type == 0:
        return None
    return (prices[-1] - moyenne) / ecart_type


def compute_signal(prices, product=None, unites=None):
    # unites = ce que je possede deja de ce produit (pour respecter le plancher)
    z = calcul_z(prices, product)
    if z is None:
        return 0.0

    # C : on vise un stock (0 en haut du cycle, stock_max au creux), 100 unites max par tick
    if product in stock_max and unites is not None:
        if z > seuil_vente.get(product, seuil):
            return round(-min(1.0, unites / 100), 3) + 0.0
        if z < -seuil:
            return round(max(0.0, min(1.0, (stock_max[product] - unites) / 100)), 3)
        return 0.0

    if z > 0 and z < seuil_vente.get(product, seuil):
        return 0.0   # pas encore assez haut pour vendre
    if z <= 0 and z > -seuil:
        return 0.0   # pas assez bas pour acheter

    # on fait l'inverse de z, et on reste entre -1 et 1
    s = max(-1.0, min(1.0, -z))

    # vente : on ne descend jamais sous le plancher (on garde une partie du stock)
    if s < 0 and product in plancher and unites is not None:
        a_vendre = max(0.0, unites - plancher[product])
        s = max(s, -a_vendre / 100)
    return round(s, 3) + 0.0   # + 0.0 transforme -0.0 en 0.0


def apply_budget_guard(raw_signal, money, price, reserve=MIN_MONEY_RESERVE):
    # si on veut acheter plus que ce qu'on peut payer (sans toucher a la reserve)
    # on reduit le signal au lieu de le mettre a 0, comme ca on achete quand meme un peu
    # les ventes on n'y touche pas, vendre ca rapporte de l'argent donc pas de risque
    if raw_signal <= 0:
        return raw_signal
    if money <= reserve:
        return 0.0
    max_affordable_units = (money - reserve) / price
    desired_units = 100 * raw_signal
    if desired_units <= max_affordable_units:
        return raw_signal
    return round(max_affordable_units / 100, 3)

def main():
    market = fetch_json(MARKET_URL)
    holdings = fetch_json(HOLDINGS_URL)
    products = market["products"]
    history = market["history"]

    decision = {}
    for p in products:
        prices = [row[p] for row in history]
        raw = compute_signal(prices, p, holdings["products"].get(p, 0))
        current_price = prices[-1] if prices else 0
        decision[p] = round(apply_budget_guard(raw, holdings["money"], current_price), 3)

    decision["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with open("decision.json", "w") as f:
        json.dump(decision, f, indent=2)
    print(f"Wrote decision.json: {decision}")

    # on note tout dans le journal pour le dashboard (si ca plante, pas grave,
    # la decision est deja ecrite)
    try:
        ajouter_au_journal(market, holdings, decision)
    except Exception as e:
        print("journal pas mis a jour :", e)


def raison(p, z, d):
    # petite phrase pour expliquer la decision dans le journal
    if p not in fenetre:
        return f"{p} : pas de cycle, on ne trade pas"
    if z is None:
        return f"{p} : pas assez d'historique"
    if p in stock_max:
        sv = seuil_vente.get(p, seuil)
        if z > sv and d < 0:
            return f"{p} : z={z:+.2f} haut du cycle, on vend tout avant le pic ({-d})"
        if z > sv:
            return f"{p} : z={z:+.2f} haut du cycle, plus rien a vendre, on attend le creux"
        if z > 0:
            return f"{p} : z={z:+.2f} ca monte, on attend z > {sv} pour vendre"
        if z < -seuil and d > 0:
            return f"{p} : z={z:+.2f} creux, on remonte vers {stock_max[p]} unites, achat {d}"
        if z < -seuil:
            return f"{p} : z={z:+.2f} creux mais deja au stock max (ou plus d'argent)"
        return f"{p} : z={z:+.2f} ca redescend, on attend le creux pour racheter"
    if d == 0 and abs(z) < seuil:
        return f"{p} : z={z:+.2f} proche de la moyenne, on attend"
    if d == 0 and 0 < z < seuil_vente.get(p, seuil):
        return f"{p} : z={z:+.2f} ca monte encore, on attend le pic pour vendre"
    if d == 0 and z > 0:
        return f"{p} : z={z:+.2f} prix haut mais on garde le plancher de {plancher.get(p, 0)}"
    if d == 0:
        return f"{p} : z={z:+.2f} mais plus d'argent pour acheter"
    if d > 0:
        return f"{p} : z={z:+.2f} prix bas, on achete {d}"
    return f"{p} : z={z:+.2f} prix haut, on vend {-d}"


def combler_trous(journal, market):
    # Le workflow GitHub ne tourne pas toujours a l'heure et le marche peut avancer
    # de plusieurs ticks d'un coup : certains ticks ne sont jamais vus par le script.
    # Pendant ces ticks, le marche a reutilise notre dernier decision.json, donc on
    # rejoue cette meme decision tick par tick (execution au prix du tick, achat
    # limite par l'argent, vente limitee par les unites) pour reconstruire les lignes.
    # Verifie sur les trous 360->362 et 372->376 : on retombe sur l'argent et les
    # unites reels de la ligne suivante (a 1 centime pres).
    # Les lignes ajoutees ont "simule": true. Renvoie le nombre de lignes ajoutees.
    par_tick = {row["t"]: row for row in market["history"]}
    produits = market["products"]
    ajoutees = 0
    i = 0
    while i < len(journal) - 1:
        avant, apres = journal[i], journal[i + 1]
        if apres["tick"] - avant["tick"] <= 1:
            i += 1
            continue
        argent = avant["argent"]
        unites = {p: avant["produits"].get(p, 0) for p in produits}
        nouvelles = []
        for t in range(avant["tick"] + 1, apres["tick"]):
            if t not in par_tick:
                break   # prix inconnu, on ne peut pas simuler plus loin
            prix = {p: par_tick[t][p] for p in produits}
            # la decision en cours (celle de la ligne d'avant) s'execute a ce tick
            for p in produits:
                q = 100 * avant["decision"].get(p, 0)
                q = min(q, argent / prix[p]) if q > 0 else max(q, -unites[p])
                argent -= q * prix[p]
                unites[p] += q
            history = [row for row in market["history"] if row["t"] <= t]
            z = {}
            for p in produits:
                zp = calcul_z([row[p] for row in history], p)
                z[p] = None if zp is None else round(zp, 3)
            nouvelles.append({
                "tick": t,
                "date": par_tick[t].get("timestamp"),
                "prix": prix,
                "z": z,
                "decision": dict(avant["decision"]),   # pas de nouveau decision.json
                "raison": ["tick non vu par le script : la décision précédente a été réappliquée (ligne simulée)"],
                "argent": round(argent, 2),
                "produits": {p: round(q, 2) for p, q in unites.items()},
                "valeur": round(argent + sum(unites[p] * prix[p] for p in produits), 2),
                "baselines": {b: None for b in ["random_trader", "random_buyer", "random_seller"]},
                "simule": True,
            })
        journal[i + 1:i + 1] = nouvelles
        ajoutees += len(nouvelles)
        i += len(nouvelles) + 1
    if ajoutees:
        print(f"journal : {ajoutees} tick(s) manquant(s) simule(s)")
    return ajoutees


def ajouter_au_journal(market, holdings, decision):
    history = market["history"]
    tick = history[-1]["t"]
    prix = {p: history[-1][p] for p in market["products"]}

    try:
        with open(JOURNAL) as f:
            journal = json.load(f)
    except FileNotFoundError:
        journal = []

    # le marche avance toutes les heures mais le script tourne toutes les 30 min,
    # donc on n'ajoute une ligne que quand il y a un nouveau tick
    # (on comble quand meme les trous au cas ou il en reste d'un ancien passage)
    if journal and journal[-1]["tick"] == tick:
        if combler_trous(journal, market) > 0:
            with open(JOURNAL, "w") as f:
                json.dump(journal, f, indent=1)
        else:
            print("journal : meme tick, rien a ajouter")
        return

    # valeur des autres traders (dont les 3 baselines) donnee par le classement du prof
    classement = fetch_json(LEADERBOARD_URL)["leaderboard"]
    autres = {row["id"]: row["total_value"] for row in classement}

    valeur = holdings["money"] + sum(holdings["products"][p] * prix[p] for p in prix)
    z = {}
    for p in market["products"]:
        prices = [row[p] for row in history]
        zp = calcul_z(prices, p)
        z[p] = None if zp is None else round(zp, 3)

    journal.append({
        "tick": tick,
        "date": decision["updated_at"],
        "prix": prix,
        "z": z,
        "decision": {p: decision[p] for p in market["products"]},
        "raison": [raison(p, z[p], decision[p]) for p in market["products"]],
        "argent": round(holdings["money"], 2),
        "produits": {p: round(q, 2) for p, q in holdings["products"].items()},
        "valeur": round(valeur, 2),
        "baselines": {b: autres.get(b) for b in ["random_trader", "random_buyer", "random_seller"]},
    })
    combler_trous(journal, market)
    with open(JOURNAL, "w") as f:
        json.dump(journal, f, indent=1)
    print(f"journal : tick {tick} ajoute ({len(journal)} lignes)")

if __name__ == "__main__":
    main()
