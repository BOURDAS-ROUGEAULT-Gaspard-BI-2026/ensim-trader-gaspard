import json, time
import requests

# les 2 liens donnes par le prof (prix du marche + mon portefeuille)
MARKET_URL = "https://raw.githubusercontent.com/exofri/ensim-trade-center/refs/heads/main/docs/data/market.json"
HOLDINGS_URL = "https://raw.githubusercontent.com/exofri/ensim-trade-center/refs/heads/main/traders/gaspard/holdings.json"

MIN_MONEY_RESERVE = 50 # on garde toujours au moins 50 d'argent, on n'achete pas en dessous

def fetch_json(url):
    resp = requests.get(url, proxies={"http": None, "https": None}, timeout=(10, 20))
    resp.raise_for_status()
    return resp.json()

# en regardant les courbes de prix :
# - A monte et descend en cycle (environ 48 ticks entre 2 sommets)
# - C fait pareil mais plus vite (environ 24 ticks)
# - B ne fait pas de cycle, il bouge au hasard -> on ne le trade pas
# donc pour A et C on fait du retour a la moyenne (bandes de Bollinger) :
# prix au dessus de la moyenne -> il va redescendre -> on vend
# prix en dessous de la moyenne -> il va remonter -> on achete
# fenetres choisies en testant sur les ticks 0-200, verifiees sur 200-343
fenetre = {"A": 48, "C": 12}
seuil = 0.5   # si le prix est trop proche de la moyenne on ne fait rien (bruit)


def compute_signal(prices, product=None):
    if product not in fenetre:
        return 0.0  # B : on ne trade pas
    n = fenetre[product]
    if len(prices) < n:
        return 0.0  # pas assez d'historique

    derniers = prices[-n:]
    moyenne = sum(derniers) / n
    ecart_type = (sum((x - moyenne) ** 2 for x in derniers) / n) ** 0.5
    if ecart_type == 0:
        return 0.0

    # z = de combien d'ecarts-types le prix actuel est loin de la moyenne
    z = (prices[-1] - moyenne) / ecart_type
    if abs(z) < seuil:
        return 0.0

    # on fait l'inverse de z, et on reste entre -1 et 1
    return max(-1.0, min(1.0, -z))


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
        raw = compute_signal(prices, p)
        current_price = prices[-1] if prices else 0
        decision[p] = round(apply_budget_guard(raw, holdings["money"], current_price), 3)

    decision["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with open("decision.json", "w") as f:
        json.dump(decision, f, indent=2)
    print(f"Wrote decision.json: {decision}")

if __name__ == "__main__":
    main()
