# Smart Wardrowbe

Een AI-kleerkast voor het hele gezin. Je fotografeert je kleren, de AI herkent ze, en je krijgt
outfitvoorstellen op basis van het weer. Iedereen heeft een eigen kast; wisselen gaat met één tik.

## Instellen

1. Maak een Gemini API-key op <https://aistudio.google.com/apikey>. Herkennen werkt met een
   gratis key; voor productfoto's moet facturering aan staan (zie hieronder).
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

## Productfoto's

Elke nieuwe foto, ook een vervangen foto, wordt automatisch een nette productfoto: hetzelfde
kledingstuk, gestreken, mooi gelegd, op een witte achtergrond. Dat gebeurt nadat de AI het stuk
herkend heeft; in de kast staat dan even *Productfoto maken…*.

- **Kost:** Gemini rekent hiervoor enkele centen per foto (ongeveer € 0,06 met het standaardmodel).
  Het werkt alleen als facturering aan staat in Google AI Studio.
- **Origineel blijft bewaard.** Bij een stuk tik je op het pijltje terug om de originele foto te
  zien. Met de toverstaf maak je opnieuw een productfoto, altijd vanaf het origineel.
- **Mislukt het?** Dan houdt het stuk gewoon je eigen foto en blijft het bruikbaar in outfits.
  Probeer het later opnieuw met de toverstaf; de reden staat in het **Logboek**.
- **Stof:** de AI krijgt naast je foto ook een close-up van de stof mee, en moet weefsel,
  dikte, glans en kleur daarvan overnemen. Wil je het nog scherper, zet dan *Scherpte van
  productfoto's* op **2K** (ongeveer € 0,09 per foto). Een scherpe foto bij daglicht helpt het
  meest: wat de camera niet ziet, kan de AI niet natekenen.
- **Uitzetten:** zet *Automatische productfoto's* uit op het tabblad **Configuratie**. De
  toverstaf blijft dan werken per stuk.
- Het is een AI-tekening van je foto, geen echte foto. Kijk bij een stuk met veel details of
  print of het klopt; anders zet je het origineel terug.

## Buitenshuis (4G)

Gebruik de **Tailscale**-app voor Home Assistant en de Tailscale-app op je gsm. Open dan
`http://<Tailscale-IP van Home Assistant>:3000`. Zet de app nooit rechtstreeks open op het
internet: er is geen wachtwoord.

## Goed om te weten

- **Geen wachtwoord.** Iedereen op je wifi kan de app openen. Zet poort 3000 niet open naar het
  internet.
- **Foto's gaan naar Google Gemini** om de kleding te herkennen en om de productfoto te maken.
  Al de rest blijft op je Home Assistant.
- **Back-ups:** de app en al haar gegevens zitten in je Home Assistant-back-ups. Tijdens een
  back-up stopt de app even, zodat de database netjes bewaard wordt.
- **Geheugen:** de app gebruikt ongeveer 1,5 à 2 GB RAM. Het bouwen bij installatie en updates
  vraagt tijdelijk meer.

## Problemen

Kopieer de laatste regels uit het tabblad **Logboek**; daar staat wat er misliep.
