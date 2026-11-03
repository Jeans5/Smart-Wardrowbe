# Smart Wardrowbe

Een AI-kleerkast voor het hele gezin. Je fotografeert je kleren, de AI herkent ze, en je krijgt
outfitvoorstellen op basis van het weer. Iedereen heeft een eigen kast; wisselen gaat met één tik.

## Instellen

1. Maak een gratis Gemini API-key op <https://aistudio.google.com/apikey>.
2. Vul op het tabblad **Configuratie** in:
   - **Gemini API-key**: de key van stap 1.
   - **Gezinsleden**: de namen, gescheiden door komma's.
   - **Adres van de app**: `http://<IP-adres van Home Assistant>:3000`. Het IP-adres zie je in de
     adresbalk als je Home Assistant opent, bv. `http://192.168.0.10:3000`.
3. Klik op **Opslaan** en daarna op **Starten**.

De eerste start na een installatie of update duurt even: de app bouwt zichzelf en zet de
database klaar. Volg het in het tabblad **Logboek**; als daar `Ready` staat, is ze klaar.

## Gebruiken

Open het adres van de app op de tablet en kies in de browser **Toevoegen aan beginscherm**.

- **Aanmelden:** tik op je naam.
- **Wisselen:** tik rechtsboven op **Wissel profiel**.
- **Taal:** kies **Nederlands** in het taalmenu bovenaan als de app nog in het Engels staat.
- **Gezin:** maak bij *Gezin* een gezin aan en deel de uitnodigingscode, dan zien jullie elkaars
  outfits.

## Goed om te weten

- **Geen wachtwoord.** Iedereen op je wifi kan de app openen. Zet poort 3000 niet open naar het
  internet.
- **Foto's gaan naar Google Gemini** om de kleding te herkennen. Al de rest blijft op je
  Home Assistant.
- **Back-ups:** de app en al haar gegevens zitten in je Home Assistant-back-ups. Tijdens een
  back-up stopt de app even, zodat de database netjes bewaard wordt.
- **Geheugen:** de app gebruikt ongeveer 1,5 à 2 GB RAM. Het bouwen bij installatie en updates
  vraagt tijdelijk meer.

## Problemen

Kopieer de laatste regels uit het tabblad **Logboek**; daar staat wat er misliep.
