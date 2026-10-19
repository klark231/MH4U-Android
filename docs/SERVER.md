# Multiplayer servers

MH4U's local play (the Gathering Hall, quests together) goes over the internet through Azahar's network rooms. This page is for whoever runs the servers for a group of players: a room, which carries the game, and optionally a lobby, which lists the rooms. Players who only want to join: [Using a lobby](#players-using-a-lobby) below, and [PLAYING.md](PLAYING.md#4-multiplayer).

## The room and the lobby

- **A room** relays the game. Every player's Azahar connects to it over UDP, and the game's local-wireless traffic between their consoles goes through it, so the room should be where every player has a good connection. Azahar's *Multiplayer → Create Room* makes one inside the host's Azahar; Azahar's dedicated room server, `azahar-room` (in the port's Windows package), runs one without a game, on any PC or server, for as long as you like. *Create Room* takes 2 to 16 players (8 by default), the dedicated room 16 by default and up to 254. A room can have a password, and its host can kick and ban players by name or address.
- **A lobby** (`server/lobby/mh4u_lobby.py`) is the list behind *Multiplayer → Browse Public Rooms*: rooms announce themselves to it (and then their players, every 15 seconds) and Azahar fetches the list from it. It also vouches for names: a player signed in to the lobby gets a token that a room checks when they join, so the room shows their lobby name next to their nickname, its host can ban that name, and the lobby's moderators are moderators in every room that uses it. No game traffic goes through a lobby. It is one Python file with no dependencies, light enough for the smallest VPS or a Raspberry Pi.

A lobby is optional: without one, the host's room is *Unlisted* and players type its address in *Multiplayer → Direct Connect to Room*. Azahar used to list rooms with Citra's web service, which is gone, and has no lobby server of its own; `mh4u_lobby.py` speaks the same web API, so the Azahar in the port's packages, and official Azahar, use it unchanged once players give its address. The port's own app uses the same lobby, in its launcher and its in-game menu, and asks it for the host's public address (`GET /ip`) for the invite it shows.

## Ports

| Server | Protocol and port | Who must reach it |
|---|---|---|
| Room | UDP 24872 (`port` in the scripts, `--port` for `azahar-room`) | every player |
| Lobby | TCP 24872 (`--port`) | every player, and every room that announces itself |

At home, forward the port on your router to the PC that runs the server (the router's *port forwarding* or *virtual server* page), and allow the program when the firewall asks (Windows: `azahar-room.exe`, and Python for the lobby). Using the same number for both is deliberate: one rule for TCP and UDP 24872 covers a PC that runs both. Rooms are IPv4 only (Azahar's network library). Behind a provider's shared address (CGNAT: the router's WAN address isn't your public IP) nothing can be forwarded: use a VPS, or put all players in one virtual network (Tailscale, ZeroTier, Radmin VPN) and use the host's address in it.

## Host a room on your PC

**Windows**, with the port's Windows package:

1. Put `Host room.bat` and `host-room.ps1` from `server/windows/` next to `azahar-room.exe` in the package, or in a folder inside it (the script looks next to itself and in the folder above).
2. Double-click `Host room.bat`. The first run writes `%APPDATA%\MH4U Server\host-room.ini` with the defaults (room *MH4U Gathering Hall*, UDP 24872, 16 players, no password, not in a lobby) and starts the room; change them there with Notepad, or for one run: `"Host room.bat" name="Night hunts" password=secret`. The room is set up for Monster Hunter 4 Ultimate (`--preferred-app "Monster Hunter 4 Ultimate" --preferred-app-id 0004000000126300`) and keeps its bans in `room-ban-list.txt` next to the settings. The settings hold the room's password and lobby token: `%APPDATA%` belongs to your Windows account, but administrators and any program you run can read it, as with all your files, and the script doesn't narrow its permissions further.
3. Forward UDP 24872 on your router to this PC (the script prints the PC's address on your network).
4. Players: *Multiplayer → Direct Connect to Room*, your public IP address and port 24872. To list the room in a lobby instead, set `lobby_url` (e.g. `http://lobby.example.org:24872`), `username` and `token` in `host-room.ini`: the room's name and token in that lobby, as a player has (see [Using a lobby](#players-using-a-lobby); in an open lobby the first use claims the name). Before you choose that name, read [Moderators](#moderators).
5. To close the room, type `q` and Enter in its window: it leaves the lobby and saves its bans. Closing the window works too, but forgets the bans made since it started.

**Linux and macOS**: `server/unix/host-room.sh` does the same (settings in `~/.local/share/mh4u-server/host-room.ini`, on a Mac `~/Library/Application Support/MH4U Server/`, readable by you alone). It needs `azahar-room` (or the `azahar-room.AppImage` Azahar's Linux bundling makes) next to it, in the folder above, on the `PATH` or in `$AZAHAR_ROOM`. The port's Linux and macOS packages don't include it yet: build it from Azahar's source (`cmake --build build --target citra_room_standalone`, with `ENABLE_WEB_SERVICE` on, the default), or run the room with Docker (below).

*Create Room* in Azahar is the simplest way to host when you play anyway; the dedicated room keeps running when the host's game closes or crashes, and runs on a machine that doesn't play.

## Run a lobby

```bash
python3 server/lobby/mh4u_lobby.py                  # TCP 24872, state in ./lobby-data
python3 server/lobby/mh4u_lobby.py --data /srv/mh4u-lobby --mode invite
```

On Windows, `server\windows\Run lobby.bat` runs it with Python 3.10 or newer from python.org, its state in `%APPDATA%\MH4U Server\lobby`; on Linux and macOS, `server/unix/run-lobby.sh`, its state next to the room's settings. Both pass their arguments on (`"Run lobby.bat" --mode invite`, `./run-lobby.sh add-user "Hunter One"`). Players then use `http://<the lobby's address>:24872` as their Web API URL.

| Option | |
|---|---|
| `--mode open` | the default: a name belongs to whoever signs in with it first, with the token they chose |
| `--mode invite` | only names you add (`add-user`) or that a new player claims with a one-time `invite` token |
| `--data DIR` | the lobby's state (default `$MH4U_LOBBY_DATA`, or `lobby-data` in the current folder) |
| `--host ADDR`, `--port N` | where it listens (all addresses, TCP 24872) |
| `--games IDS` | list only rooms for these games, program ids in hex, comma-separated (MH4U USA: `0004000000126300`) |
| `--room-ttl S` | a room that hasn't reported for S seconds (60) leaves the list; Azahar reports every 15 s |
| `--public-address HOST` | rooms that announce themselves from a loopback or private address, i.e. rooms on the lobby's machine or network, are listed at HOST (a VPS that runs rooms next to the lobby). Not for a LAN party, where the private addresses are the right ones |
| `--trust-proxy` | behind a reverse proxy: the client's address is the last `X-Forwarded-For` entry (over all such headers), for requests that come from a loopback or private address; it must be a global address |

The commands work while the lobby runs (it reads its names again when they change):

```bash
python3 mh4u_lobby.py add-user "Hunter One"              # prints that name's token
python3 mh4u_lobby.py add-user "Hunter One"              # again: a new token; the old one stops working
python3 mh4u_lobby.py add-user "Luis" --moderator        # a moderator in every room that checks names here
python3 mh4u_lobby.py invite                             # prints a token that claims one free name, once
python3 mh4u_lobby.py remove-user "Hunter One"           # and nobody may claim the name again
python3 mh4u_lobby.py remove-user "Hunter One" --free    # ... or anyone may (open mode)
python3 mh4u_lobby.py list-users
```

(with the same `--data` as the lobby). Names follow Azahar's rule for usernames: 4 to 20 letters, digits, spaces, `.`, `_` or `-`. Names that differ only in capitals or in runs of spaces count as one, so a look-alike of a taken name is refused, and a new name can't start or end with a space or have two in a row. `remove-user` blocks the name, even one nobody has yet (to keep, say, `Lobby Admin` for yourself); `add-user` gives a blocked name out again. `invite` suits invite mode when you don't know a player's Azahar username: they sign in with their own name and the invite token, which then stays their token.

The data folder holds `users.json` (names, salted hashes of their tokens, unused invites, blocked names), `lobby_key.pem` (the RSA key that signs the tokens rooms check; made on the first start) and `jwt_secret` (signs the lobby's own sign-in tokens). The lobby makes the folder and these files readable by its own user alone (on Windows they inherit the folder's permissions, as above). Back the folder up, and keep it across updates: a room fetches the key's public half when it starts, so with a new key, running rooms stop recognising names until they restart.

What rooms send is cleaned to what every Azahar client can show and parse: a room's name is cut at 20 characters, as Azahar's own rooms allow; control and invisible formatting characters (right-to-left overrides, zero-width spaces), unpaired surrogates (one of them makes Azahar refuse the whole list) and `|` (Azahar's Android app joins a room's fields with it) are taken out; a room named like a listed one gets a number, ` (2)`, because the Android app shows only one room per name; a room lists no more players than it takes, and a player whose nickname isn't one Azahar allows is left out.

The lobby writes one line per request to its standard error: the client's address, the request, the status, the name signed in, the time taken and what happened (a room listed, a name claimed). Never a token. A container's health check (`GET /health` from the container itself) isn't logged.

### Limits

Against floods and guessing, the lobby limits what one client address, and one name, can do:

| What | Limit |
|---|---|
| connections at once | 16 per address; 256 in all (fewer if the process may open fewer files) |
| a request | its line and headers within 10 s and 16 KiB, its body within 10 s more and 64 KiB |
| requests | 600 a minute per address; then 429 for the rest of the minute, logged once |
| sign-ins (each costs a slow hash) | 60 per address and 30 per name in 10 minutes; 20 failed ones per address |
| new names | 10 an hour per address |
| room tokens (each costs an RSA signature) | 120 per address and 60 per name in 10 minutes |
| rooms | 10 per address and 10 per name; 500 in all |

Every Azahar behind one address counts together: players behind one home router or a provider's shared address (CGNAT), and every client of a reverse proxy unless the lobby runs with `--trust-proxy`, which then counts each client by its own address (the proxy's connections only count towards the total). Hence the limits per name as well: a name is limited wherever it signs in from.

## A server with Docker

`server/docker/compose.yaml` runs a lobby and one room on a server with Docker and Compose:

1. Copy the repository to the server (`git clone`; the room's image takes the Azahar commit from `port/azahar/COMMIT`).
2. `cd server/docker && cp example.env .env`, and in `.env` set `PUBLIC_ADDRESS` to the server's public IP or host name, `LOBBY_USERNAME` and `LOBBY_TOKEN` to the room's name in its lobby (there is no default: read [Moderators](#moderators)) and a long random string, and the room's name, password and size as you like. A value runs to the end of its line, and Compose replaces `$NAME` in it: put a value with a `$` in single quotes (`ROOM_PASSWORD='pa$$word'`).
3. `docker compose up -d --build`. The first build compiles Azahar's room server: it downloads Azahar's build environment (the image Azahar's CI uses, about 4 GB, pinned to one version) and Azahar's source, applies the port's patch to Azahar (`port/azahar/azahar.patch`, which carries the port's fixes to rooms), and takes a while; later starts take seconds.
4. Open TCP 24872 and UDP 24872 in the server's firewall.
5. Check it: `docker compose logs -f`, and `http://PUBLIC_ADDRESS:24872/lobby` in a browser lists the room.

The room announces itself to the lobby over the containers' own network, so the lobby sees it at a private address; `PUBLIC_ADDRESS` is what it lists instead (`--public-address`). `ROOM_PORT` must be the same number inside and outside the room's container, because the room tells the lobby its own port. `docker compose stop` (or `restart`) gives the room time to leave the lobby and save its bans (`ban-list.txt` in its volume). Docker keeps the last 30 MB of each service's log (`docker compose logs`).

In invite mode (`LOBBY_MODE=invite`), the room's name has to exist before the room starts: `docker compose exec lobby mh4u-lobby add-user "<the room's name>"`, put the token it prints in `LOBBY_TOKEN`, then `docker compose up -d room`. Players' names: `docker compose exec lobby mh4u-lobby invite`, or `add-user` with their username. A room announces itself only once, when it starts: if the lobby turns it away, it runs unlisted until `docker compose restart room`.

The images also work on their own: `docker build -f server/docker/Dockerfile.lobby -t mh4u-lobby .` from the repository's top folder, `docker run -d -p 24872:24872 -v mh4u-lobby:/data mh4u-lobby`; the room's settings are the environment variables listed in `server/docker/room-entrypoint.sh`. Only the files they need go into the build (the `*.dockerignore` files), never the game data a checkout may have in `baserom/` or `build/`.

## A lobby and several rooms

More rooms on the same server: copy the `room` service in `compose.yaml` under another name, with its own `ROOM_PORT` (and port line), `ROOM_NAME` and volume:

```yaml
  room2:
    image: mh4u-room
    restart: unless-stopped
    depends_on: {lobby: {condition: service_healthy}}
    environment: {ROOM_NAME: "MH4U High Rank", ROOM_PORT: "24873", LOBBY_URL: "http://lobby:24872",
                  LOBBY_USERNAME: "${LOBBY_USERNAME}", LOBBY_TOKEN: "${LOBBY_TOKEN}"}
    ports: ["24873:24873/udp"]
    volumes: ["room2-data:/data"]
    stop_grace_period: 30s
    logging: *logging
```

(and `room2-data:` under `volumes:`). Up to 10 rooms can share one name in the lobby, at different ports. Rooms elsewhere, on players' PCs or other servers, use the host scripts with `lobby_url = http://lobby.example.org:24872` and a name and token of their own. A room is listed at the address its announcement comes from, and moves when its reports start coming from another one (a home connection's new address): it must reach the lobby the way players reach the room (not through a VPN that only the lobby's machine is in).

## Moderators

A room's moderators can kick and ban its players. Azahar makes moderators of the players a lobby vouches for as moderators (`add-user --moderator`), of a player whose checked name (signed in to the lobby) is the room's username, and, at the commit the port pins, of **any player whose nickname is the room's username** (`HandleJoinRequest` in Azahar's `src/network/room.cpp`). That last rule lets the host of a *Create Room* room moderate it without a lobby, but a nickname is anyone's to type, and a lobby shows a listed room's username as its owner. A host playing in their own *Create Room* room is safe while they are in it, since a room lets in one player per nickname; a dedicated room has no such player, so anyone who reads its owner in *Browse Public Rooms* can join under that nickname and kick or ban the others.

The port has a fix, proposed for `port/azahar/azahar.patch`: the host's rights go only to the room's username as a checked name, or to the host's own Azahar joining from the room's machine. Once it is there, the port's packages and the Docker room (`Dockerfile.room` applies the port's patch) have it, and you moderate your dedicated room by joining it with the room's username and token as your Azahar username and Network Token. Until then, give a listed dedicated room a password, so that only players you told get in, or keep it unlisted (Direct Connect); a room's console says `User ... is a moderator` whenever it makes one.

## HTTPS

Tokens travel in request headers (a player's token in `x-token` when signing in, the lobby's sign-in token in `Authorization` after that), so over plain HTTP anyone on the way can read them. A reverse proxy with a certificate in front of the lobby protects clients that speak HTTPS. **Azahar, at the commit the port uses, doesn't**: its web client (`src/web_service/web_backend.cpp`) opens its connection with only the host and port of the Web API URL, so a URL that starts with `https://` gets plain HTTP, sent to port 443, which an HTTPS server refuses. It also ignores a path in the URL: it asks for `/lobby` and `/jwt/...` at the root, so a lobby needs its own host name or port, not `https://example.org/mh4u`. Until that changes:

- Players' Azahar uses the lobby over plain HTTP, as above. In an open lobby a token only protects a name in that lobby: tell players to choose one they don't use anywhere else. The tokens `add-user` and `invite` make are random.
- For HTTPS clients (browsers, the port's own app, a later Azahar), also put a reverse proxy in front and run the lobby with `--trust-proxy`, so that it still sees players' addresses. Plain HTTP on 24872 and HTTPS through the proxy then work side by side: the lobby takes the proxy's `X-Forwarded-For` only from a loopback or private address. With [Caddy](https://caddyserver.com/), which gets a certificate on its own:

  ```
  lobby.example.org {
      reverse_proxy 127.0.0.1:24872
  }
  ```

  With nginx:

  ```nginx
  server {
      listen 443 ssl;
      server_name lobby.example.org;
      ssl_certificate     /etc/letsencrypt/live/lobby.example.org/fullchain.pem;
      ssl_certificate_key /etc/letsencrypt/live/lobby.example.org/privkey.pem;
      location / {
          proxy_pass http://127.0.0.1:24872;
          proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
      }
  }
  ```

## Players: using a lobby

With the port's Azahar (or official Azahar, which has the same multiplayer code):

1. *Emulation → Configure → System → Username*: your name (4 to 20 letters, digits, spaces, `.`, `_`, `-`). It's your name in rooms and in the lobby.
2. *Emulation → Configure → General*, tab *Network*: **Network Web API URL** = the lobby's address, e.g. `http://lobby.example.org:24872` (`http://`, and the port). **Network Token**: in an open lobby, a password of your own (the first time it claims your username in that lobby; keep using the same one); in an invite lobby, the token its host gave you. Then *OK*.
3. *Multiplayer → Browse Public Rooms* lists the rooms; double-click one to join it (a room with a lock asks for its password). Browsing and joining work without a token too; rooms then show just your nickname, unchecked.
4. To host a listed room in your own Azahar: *Multiplayer → Create Room*, *Room Type* **Public**, port 24872 (forwarded to your PC as above), a password if you like, *Host Room*. Hosting a public room needs the token. In the port's Azahar, *Public* is greyed out while the Web API URL is empty; if you opened *Create Room* before setting it, restart Azahar.
5. In the game: *Local Play* at the Gathering Hall ([PLAYING.md](PLAYING.md#4-multiplayer)).

**With the port's app**: put the lobby's address and your token in the launcher's *Online* screen (*Your name and lobby*), or `lobby` and `token` in `mh4u-app.ini`. The menu's *Multiplayer* tab (Esc, in the game) and the launcher list the lobby's rooms, each with *Join*, and host listed rooms. When you host, the app asks the lobby for your public address (`GET /ip`) to put in the invite it shows; a lobby older than that, or one on your own network, can't tell it, and the tab says so.

| Message or symptom | Cause |
|---|---|
| *Failed to announce the room to the public lobby. Debug Message: Credentials needed* (or *401*) | the lobby didn't take your name with your token: in an open lobby someone else has that name (or you changed your token), in an invite lobby the name wasn't added; or too many failed sign-ins from your address (wait 10 minutes). A dedicated room logs `UpdateJWT failed` and stays unlisted |
| ... *403* / *429* | the lobby lists only rooms for other games (`--games`) / too many rooms or requests from your address or name ([Limits](#limits)) |
| The room is listed, but nobody gets in | its UDP port isn't forwarded, or it's listed at a private address (the lobby's log then says so: `--public-address`) |
| *Browse Public Rooms* stays empty | the Web API URL is wrong (`http://`, host, port) or the lobby is down: open the URL with `/lobby` added in a browser |
| A room shows players with a token as unchecked (no lobby name next to the nickname) | it couldn't fetch the lobby's key when it started (the lobby was down, or its key changed since): restart the room |
