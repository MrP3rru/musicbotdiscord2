# Discord Music Lite — Render Free

Wariant startowy: bot i audio działają na Render. Nie jest jeszcze wdrożony.
Python 3.11, discord.py z DAVE, yt-dlp, Deno i FFmpeg w obrazie Docker.

## Ograniczenia

- Jeden skonfigurowany serwer. Dowolny zwykły kanał głosowy, ale tylko jeden naraz.
  Bot dołącza do kanału osoby używającej `/play`. Sterowanie wymaga obecności na kanale bota.
- Audio wychodzące: Opus 48 kb/s, stała przepływność, bez wideo. To kompromis jakościowy;
  różnica względem wyższej jakości może być słyszalna, szczególnie w muzyce stereo.
- Maksymalnie 5 minut na film, 3 oczekujące linki, jedno dodanie co 15 sekund globalnie.
- Tylko pojedyncze linki YouTube; bez playlist, live, wyszukiwania i zapętlania.
- Pusty kanał lub wszyscy ludzie wyciszeni (mikrofon ALBO odsłuch, lokalnie lub przez serwer):
  zatrzymanie po zdarzeniu Discord, awaryjna kontrola co 5 sekund. Proces audio zostaje zamknięty,
  kolejka wyczyszczona, bot rozłączony. Wznowienie wyłącznie nowym `/play link:...`, od początku utworu.
  Co najmniej jedna osoba musi mieć włączony mikrofon i odsłuch. Boty nie liczą się jako słuchacze.
  Samo wyciszenie mikrofonu nie oznacza nieobecności — ta reguła jest celowym ograniczeniem.
- Rozłączenie po 60 sekundach pustej kolejki (kontrola co 5 sekund).
- Budżet 120 minut dziennie UTC; pełna długość rezerwowana przy starcie, również po pominięciu.
  Ostatnie około 5 minut budżetu stanowi bufor. Licznik jest w pamięci: restart/deploy go zeruje.
  To ograniczenie pomocnicze, NIE trwały licznik transferu ani zabezpieczenie rozliczeń Render.
- Brak automatycznego ponawiania nieudanego utworu. Pobieranie metadanych maks. 40 sekund.
- Brak plików muzycznych na dysku. yt-dlp może potrzebować aktualizacji, a YouTube może odrzucić IP hostingu.

48 kb/s daje około 21,6 MB audio na godzinę, czyli 1,30 GB przy 60 godzinach grania.
Do tego dochodzą nagłówki pakietów, szyfrowanie, połączenie Discord i inne odpowiedzi.
Nowy plan Render Hobby ma 5 GB miesięcznie współdzielone przez workspace:
https://render.com/docs/outbound-bandwidth — sprawdź faktyczny limit konta w Billing.
Liczba słuchaczy tego samego kanału nie mnoży strumieni wysyłanych przez bota.

## Wdrożenie

1. W Discord Developer Portal otwórz swoją aplikację, zakładkę Bot. Token ustaw później jako
   sekret na Render. Nie wpisuj go do repozytorium ani czatu. Privileged Gateway Intents nie są potrzebne.
2. W OAuth2 URL Generator wybierz `bot` i `applications.commands`, a uprawnienia:
   View Channels, Send Messages, Connect, Speak. Zaproś bota na swój serwer. Administrator nie jest potrzebny.
3. W Discord włącz Ustawienia → Zaawansowane → Tryb dewelopera. Skopiuj ID serwera.
   ID kanału nie jest potrzebne.
4. Umieść pliki projektu w swoim repozytorium GitHub (bez `.env` i `.venv`).
5. Render → New → Web Service → wybierz repo → Runtime: Docker → Instance: Free.
   Alternatywnie użyj New → Blueprint z dołączonym `render.yaml`.
6. W Environment ustaw `DISCORD_TOKEN`, `GUILD_ID`.
   Health Check Path: `/health`. Nie ustawiaj Build Command ani Start Command — odpowiada za nie Dockerfile.
7. Deploy. Otwórz `https://NAZWA.onrender.com/health`. Pole `discord_ready` powinno mieć wartość `true`.
   HTTP 200 oznacza działający proces HTTP, a nie potwierdzenie sprawności audio.
8. UptimeRobot → monitor HTTP(S) → ten sam adres `/health`, interwał 5 minut, jeżeli dostępny w Twoim planie.
9. Wejdź na dowolny zwykły kanał głosowy dostępny dla bota i użyj `/play link:LINK_DO_FILMU`.
   Sprawdź `/queue`, `/skip`, `/stop`. Na wszystkich tych kanałach bot potrzebuje View Channel, Connect i Speak.
10. Na Render sprawdzaj Metrics → Outbound Bandwidth oraz Billing → zużycie całego workspace.
    Bez podpiętej metody płatności przekroczenie transferu skutkuje zawieszeniem usług do kolejnego miesiąca;
    z metodą płatności może skutkować naliczeniem opłat. https://render.com/docs/free

Render może restartować darmowe usługi. Kolejka i licznik znikają po restarcie.
W razie problemu z audio sprawdź prawa Connect/Speak, połączenie głosowe oraz inny krótki film.
Nie ma gwarancji działania YouTube z IP Render. Używaj materiałów, do których odtwarzania masz prawo.

## Oddzielny serwer audio

Można przerobić projekt na klienta Lavalink: Render wysyła wyłącznie komendy,
a Lavalink pobiera audio i wysyła je bezpośrednio do Discorda. Wtedy transfer muzyki
obciąża hosting Lavalink. Samo pobieranie audio na innym serwerze i przesyłanie go przez Render
nie oszczędzi transferu wychodzącego Render. Obecny kod nie obsługuje jeszcze Lavalink.
Potrzebny jest działający węzeł z obsługą aktualnego Discord voice/DAVE i YouTube.
Publiczne darmowe węzły wymagają sprawdzenia dostępności i nie gwarantują ciągłości działania.
https://lavalink.dev/

## Testy lokalne

Zainstaluj `requirements.txt` w środowisku Python, następnie:
`python -m unittest discover -s tests -v`

Testy sprawdzają walidację linków/długości, budżet, rejestrację komend,
zatrzymywanie odtwarzania, wyciszenie/pusty kanał, brak automatycznego wznowienia
i sprzątanie procesu ekstraktora po błędzie.
Pełny test głosu wymaga tokena i kanału Discord. Docker wymaga osobnego sprawdzenia budowy obrazu.
