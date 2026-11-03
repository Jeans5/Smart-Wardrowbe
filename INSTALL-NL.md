# Smart Wardrowbe thuis installeren

Deze versie van Wardrowbe heeft een Nederlandse vertaling, gebruikt gratis Google Gemini als AI en
heeft een profielkiezer voor een gedeelde tablet: één tik op je naam en je zit in je eigen kast.

## Op Home Assistant (OS)

Heb je Home Assistant OS, dan installeer je Smart Wardrowbe als app:

1. **Instellingen → Apps → Installeer een app**, rechtsboven **⋮ → Repositories**.
2. Plak `https://github.com/Jeans5/Smart-Wardrowbe` en kies **Toevoegen**.
3. Zoek **Smart Wardrowbe** in de lijst, open het en kies **Installeren**.
4. Volg het tabblad **Documentatie** van de app.

De rest van deze pagina is voor een gewone computer met Docker.

## Wat je nodig hebt

- **Een computer die altijd aanstaat** in je thuisnetwerk (oude laptop, pc, Mac mini of NAS) met
  [Docker](https://docs.docker.com/get-docker/) erop. Op Windows: Docker Desktop met WSL.
- **Een gratis Gemini API-key** van <https://aistudio.google.com/apikey>.
- **Een tablet** op dezelfde wifi.

## Installeren

Op de computer, in een terminal:

```bash
git clone https://github.com/Jeans5/Smart-Wardrowbe.git
cd Smart-Wardrowbe
./install.sh
```

Het script vraagt drie dingen: je Gemini-key, de namen van de gezinsleden en het adres van de
computer (het stelt zelf een adres voor; meestal klopt dat). Daarna bouwt het de app, start ze en
zet de database klaar. De eerste keer duurt dat enkele minuten.

Op het einde toont het een adres zoals `http://192.168.1.20:3000`. Open dat op de tablet en kies
in de browser **Toevoegen aan beginscherm**.

## Gebruiken

- **Aanmelden:** tik op je naam. Geen wachtwoord nodig.
- **Wisselen:** tik rechtsboven op **Wissel profiel** en kies iemand anders.
- **Taal:** staat de app nog in het Engels, kies dan **Nederlands** via het taalmenu bovenaan.
- **Gezin koppelen:** maak bij *Gezin* een gezin aan en deel de uitnodigingscode met de anderen.
  Dan zien jullie elkaars outfits en kunnen jullie ze beoordelen.

## Bijwerken

```bash
git pull
./install.sh
```

Je instellingen in `.env` blijven bewaard.

## Belangrijk

Aanmelden zonder wachtwoord is bedoeld voor je **eigen thuisnetwerk**. Zet deze installatie niet
open op het internet. Wil je er onderweg bij, gebruik dan een privé-VPN zoals Tailscale.

De gratis Gemini-versie heeft limieten. Upload je veel foto's tegelijk, dan worden ze één voor
één geanalyseerd; dat kan even duren.
