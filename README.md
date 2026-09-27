# Discord Music Lite — Render Free

Wariant startowy: bot i audio działają na Render. Nie jest jeszcze wdrożony.
Python 3.11, discord.py z DAVE, yt-dlp, Deno i FFmpeg w obrazie Docker.
Generator PO `bgutil-ytdlp-pot-provider` 2.0.0 działa w tej samej usłudze na
`127.0.0.1:4416`. Nie potrzebuje tokena Discord ani ciasteczek konta Google.
Obraz kopiuje Node.js i generator z obrazu autora; wtyczka Python ma tę samą wersję.
Odtwarzanie używa klienta YouTube `mweb`. Start bota czeka na gotowość generatora;
awaria generatora kończy proces, zamiast zostawiać niedziałającego bota online.
Generator zwiększa zużycie RAM; limit sterty Node to 128 MB (nie jest to limit całego procesu).
PO token nie gwarantuje usunięcia blokady IP lub wymogu logowania YouTube.
Gdy `mweb` nie udostępnia formatu, bot wykonuje jedną próbę standardowym klientem;
nie ponawia odmowy logowania ani limitu żądań. Maksymalny czas ekstrakcji audio to
60 sekund na próbę; wyszukiwanie i playlisty mają nadal limit 40 sekund.
Sprawdzenie lokalne potwierdziło start generatora i uzyskanie URL z PO tokenem,
ale test odczytu audio otrzymał HTTP 403. Działanie na Renderze pozostaje niepotwierdzone.

Przy odtwarzaniu bot wysyła tytuł, link i długość utworu oraz przyciski **Pomiń**
i **Zatrzymaj**. Sterowanie jest dostępne tylko na tym samym kanale głosowym.
Panele zakończonych utworów są wyłączane. Bot potrzebuje Send Messages na kanale tekstowym.

## Ograniczenia

- Jeden skonfigurowany serwer. Dowolny zwykły kanał głosowy, ale tylko jeden naraz.
  Bot dołącza do kanału osoby używającej `/play`. Sterowanie wymaga obecności na kanale bota.
- Audio wychodzące: Opus 48 kb/s, stała przepływność, bez wideo. To kompromis jakościowy;
  różnica względem wyższej jakości może być słyszalna, szczególnie w muzyce stereo.
- Maksymalnie 5 minut na film, 3 oczekujące linki, jedno dodanie co 15 sekund globalnie.
- `/play utwor:reto ua` szuka 5 wyników YouTube i wybiera najczęściej oglądany spośród
  wyników o znanej długości do 5 minut. To nie gwarantuje najpopularniejszego filmu w całym YouTube.
  Można również podać pojedynczy link lub link playlisty YouTube. Live są wyłączone.
- Playlisty: bot sprawdza tylko pierwsze 10 pozycji, pomija niedostępne, duplikaty oraz filmy
  o nieznanej długości lub dłuższe niż 5 minut. Dodaje maksymalnie bieżący utwór + 3 oczekujące,
  pomniejszone o zajęte miejsca kolejki. Zachowuje kolejność playlisty i informuje o pominięciach.
  Dalsza część dużej playlisty nie jest importowana. Link filmu z `list=` importuje playlistę;
  żeby odtworzyć tylko film, usuń `list=` z linku. Prywatne playlisty wymagające logowania nie są obsługiwane.
- Autoplay jest domyślnie włączony przy nowym połączeniu: po zakończeniu utworu i wyczerpaniu
  ręcznej kolejki pobiera do 10 propozycji miksu YouTube (RD) i wybiera pierwszą pasującą,
  której nie ma w historii ostatnich 100 utworów. Bez logowania i personalizacji konta YouTube.
  Jeśli miks jest niedostępny, brak propozycji lub wystąpi błąd, bot nie ponawia go w pętli.
  `/autoplay wlacz:False` wyłącza dobieranie kolejnych piosenek. `/skip` bez ręcznej kolejki
  kończy granie, nie uruchamia rekomendacji. Limity długości i dzienne nadal obowiązują.
- Pusty kanał lub wszyscy ludzie wyciszeni (mikrofon ALBO odsłuch, lokalnie lub przez serwer):
  zatrzymanie po zdarzeniu Discord, awaryjna kontrola co 5 sekund. Proces audio zostaje zamknięty,
  kolejka wyczyszczona, bot rozłączony. Wznowienie wyłącznie nowym `/play utwor:...`, od początku utworu.
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
9. Wejdź na dowolny zwykły kanał głosowy dostępny dla bota i użyj `/play utwor:reto ua` lub podaj link.
   Sprawdź `/queue`, `/skip`, `/stop`. Na wszystkich tych kanałach bot potrzebuje View Channel, Connect i Speak.
10. Na Render sprawdzaj Metrics → Outbound Bandwidth oraz Billing → zużycie całego workspace.
    Bez podpiętej metody płatności przekroczenie transferu skutkuje zawieszeniem usług do kolejnego miesiąca;
    z metodą płatności może skutkować naliczeniem opłat. https://render.com/docs/free

Render może restartować darmowe usługi. Kolejka i licznik znikają po restarcie.
W razie problemu z audio sprawdź prawa Connect/Speak, połączenie głosowe oraz inny krótki film.
Nie ma gwarancji działania YouTube z IP Render. Używaj materiałów, do których odtwarzania masz prawo.

## Opcjonalna sesja YouTube na Renderze

1. Na osobnym koncie zaloguj się na YouTube w prywatnym oknie przeglądarki.
2. Do eksportu użyj rozszerzenia wskazanego w FAQ yt-dlp, np. **Get cookies.txt LOCALLY**
   dla Chrome (włącz dostęp w incognito). Eksportuj wyłącznie cookies youtube.com w formacie Netscape.
   Otwórz w tym samym oknie `https://www.youtube.com/robots.txt`, wyeksportuj cookies i zamknij okno.
   Nie wylogowuj się przed eksportem. Szczegóły:
   https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies
   https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp
3. Render → musicbotdiscord2 → Environment → Secret Files → Add Secret File.
   Filename: `youtube-cookies.txt`. Contents: zawartość wyeksportowanego pliku.
   Zapisz zmiany i wdróż najnowszy commit. Render udostępnia plik jako
   `/etc/secrets/youtube-cookies.txt`; bot wykrywa tę nazwę automatycznie.
4. Wejdź na kanał Discord i sprawdź jeden utwór przez `/play`.

Nie publikuj pliku ani jego zawartości w czacie, repozytorium lub logach.
Sesja może wygasnąć; wtedy wymień zawartość Secret File i wykonaj ponowne wdrożenie.
Możesz zrezygnować, usuwając Secret File i ponownie wdrażając usługę.
Jeśli ustawiłeś również `YOUTUBE_COOKIES_FILE`, usuń tę zmienną.
Własną ścieżkę można ustawić przez `YOUTUBE_COOKIES_FILE`, ale domyślnie nie jest to potrzebne.
Bot filtruje cookies do domen YouTube i używa prywatnej, tymczasowej kopii,
ponieważ yt-dlp zapisuje plik cookies, a sekret Rendera może być tylko do odczytu.
Z plikiem cookies bot pozostawia dobór klientów zalogowanego konta bibliotece yt-dlp,
zamiast wymuszać `mweb`. Wpis `session_file=loaded client=default` potwierdza
odczyt pliku, ale nie potwierdza ważności sesji ani zaakceptowania logowania przez YouTube.
Testy sesji używają fikcyjnych cookies; nie potwierdzają logowania do prawdziwego konta.
Uwierzytelnienie nie gwarantuje zdjęcia blokady YouTube i może skutkować ograniczeniem konta.

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
