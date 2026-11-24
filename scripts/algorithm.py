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
fenetre = {"A": 48, "B": 96, "C": 12}
seuil = 0.5   # si le prix est trop proche de la moyenne on ne fait rien (bruit)
seuil_vente = {"B": 1.0}   # pour B on attend d'etre pres du pic avant de vendre
plancher = {"B": 400}      # unites de B qu'on garde toujours (on ne vend pas en dessous)


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
    if journal and journal[-1]["tick"] == tick:
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
    with open(JOURNAL, "w") as f:
        json.dump(journal, f, indent=1)
    print(f"journal : tick {tick} ajoute ({len(journal)} lignes)")

if __name__ == "__main__":
    main()
