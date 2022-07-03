import discord
from discord import app_commands
from discord.ext import commands

import spotipy
from spotipy import SpotifyClientCredentials

import asyncio, subprocess, os, random, json, datetime
from async_timeout import timeout
from typing import List

env = []
with open("env", "r") as infile:
    for line in infile:
        env.append(str(line).strip())

client_creds = SpotifyClientCredentials(client_id=env[1], client_secret=env[2])
spotify_client = spotipy.Spotify(client_credentials_manager=client_creds)

playlist_dirs = []

schedule = {
    # Monday
    0: {
        0: "The Pit",
        1: "The Pit",
        2: "The Pit",
        3: "The Pit",
        4: "The Class"
    },
    # Tuesday
    1: {
        0: "The Overcast",
        1: "The Artifact",
        2: "The Overcast",
        3: "The Artifact",
        4: "The Class"
    },
    # Wednesday
    2: {
        0: "The Jungle",
        1: "The Bleed",
        2: "The Jungle",
        3: "The Bleed",
        4: "The Class"
    },
    # Thursday
    3: {
        0: "The Evening",
        1: "The Outfit",
        2: "The Evening",
        3: "The Outfit",
        4: "The Class"
    },
    # Friday
    4: {
        0: "The Clothing Store",
        1: "The Kick",
        2: "The Clothing Store",
        3: "The Kick",
        4: "The Class"
    },
    # Saturday
    5: {
        0: "The Warp",
        1: "The Sand",
        2: "The East",
        3: "The Smoke",
        4: "The Class"
    },
    # Sunday
    6: {
        0: "The Garden",
        1: "The Garden",
        2: "The Garden",
        3: "The Garden",
        4: "The Class"
    }
}

def update_playlist_dirs():
    if os.path.exists("./playlists"):
        for entry in os.listdir("./playlists"):
            if entry not in playlist_dirs:
                playlist_dirs.append(entry)

######################################################

class Player:
    # This class has been taken and modified from the Eviee example at
    # https://gist.github.com/EvieePy/ab667b74e9758433b3eb806c53a19f34
    def __init__(self, ctx, bot):
        # Note that ctx in this instance is likely a discord.Interaction,
        # since this bot is written with the updated slash commands in mind.
        self.bot = bot
        self.guild = ctx.guild
        self.channel = ctx.channel
        self.ctx = ctx

        self.queue = asyncio.Queue()
        self.next = asyncio.Event()
        self.now_playing = None
        self.playlist_name = ""

        self.volume = 0.5
        self.song_list = []
        self.song_paths = []

        self.metadata = {}

        self.today = None
        self.is_scheduled = True

        # This will likely break if you switch to 3.10 in the future
        bot.loop.create_task(self.player_loop())

    async def clear_queue(self):
        # We need to confirm this value is false,
        # otherwise the queue will never be empty
        self.endless = False

        while not self.queue.empty():
            await self.queue.get()

        self.song_list = []
    
    async def start_playlist(self, target: str, interaction: discord.Interaction, shuffle: bool, endless: bool):
        self.ctx = interaction
        self.endless = endless
        await self.clear_queue()

        # TODO: fix for playlist order
        song_list = os.listdir(f"./playlists/{target}")
        if shuffle:
            random.shuffle(song_list)

        paths = []
        for song in song_list:
            if song not in [".spotdl-cache", "failed_songs.txt", "metadata.json"]:
                await self.queue.put(f"./playlists/{target}/{song}")
                paths.append(f"./playlists/{target}/{song}")

                index = len(song) - 1
                while song[index] != '.':
                    index -= 1
                self.song_list.append(song[0:index])
        self.song_paths = paths
        self.playlist_name = target

        metadata_file = open(f"./playlists/{target}/metadata.json", "r")
        self.metadata = json.load(metadata_file)
        metadata_file.close()
    
    async def check_schedule(self, override: bool = False):
        if (datetime.date.now().day != self.today.day and datetime.datetime.now().hour == 0) or override:
            self.today = datetime.date.now()
            await self.clear_queue()

            weekday = datetime.datetime.now().weekday()
            date = datetime.date.now().day
            week_num = 0
            while date > 7:
                date -= 7
                week_num += 1
            
            playlist = schedule[weekday][week_num]
            return await self.start_playlist(target=playlist, interaction=self.ctx, shuffle=True, endless=True)

    async def player_loop(self):
        await self.bot.wait_until_ready()

        while not self.bot.is_closed():
            self.next.clear()

            try:
                async with timeout(300):
                    path = await self.queue.get()
                    source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(path))
            except asyncio.TimeoutError:
                # TODO: implement timeout disconnect
                pass

            source.volume = self.volume

            playing_embed = discord.Embed(title=f"Now Playing ~ {self.playlist_name}", description=self.song_list[0])
            # TODO: log failing song and look into
            #       why those songs are failing
            try:
                playing_embed.add_field(name="Link:", value=f"[Spotify]({self.metadata[self.song_list[0]]['url']})")
                playing_embed.set_footer(text="Up next: {}".format(self.song_list[1] if (len(self.song_list) > 1) else "nothing"))
                playing_embed.set_image(url=self.metadata[self.song_list[0]]["album_art"])
            except KeyError as e:
                user = await self.guild.fetch_member(99709623614849024)
                await user.send(content=f"`KeyError` occured while playing playlist *{self.playlist_name}*. Attempted song: '{self.song_list[0]}'")

            # TODO: the view might time out if the song
            # that's playing is longer than 15 minutes.
            # Will need to figure out how to pass
            # timeout=None into the view when creating
            if self.now_playing is None:
                self.now_playing = await self.guild.voice_client.channel.send(content="", embed=playing_embed, view=PlayerButtons(self.bot))
            else:
                await self.now_playing.edit(content="", embed=playing_embed, view=PlayerButtons(self.bot))

            self.guild.voice_client.play(source, after=lambda _: self.bot.loop.call_soon_threadsafe(self.next.set))
            await self.next.wait()

            try:
                last_played = self.song_list.pop(0)
            except IndexError:
                pass
            last_path = self.song_paths.pop(0)

            if self.endless:
                self.song_list.append(last_played)
                self.song_paths.append(last_path)
                await self.queue.put(discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(last_path)))
            try:
                await self.now_playing.edit(content="", embed=None) # Embed might not update properly if we don't clear it first
            except discord.errors.NotFound:
                pass

            if self.is_scheduled:
                await self.check_schedule()

class PlayerButtons(discord.ui.View):
    def __init__(self, bot):
        self.bot = bot
        super().__init__()

    def reset_buttons(self):
        for child in self.children:
            child.style = discord.ButtonStyle.primary

    def get_current_voice(self, interaction: discord.Interaction):
        for voice in self.bot.voice_clients:
            if voice.guild.id == interaction.guild.id:
                return voice
        return None

    def check_roles(self, interaction: discord.Interaction, role: int) -> bool:
        if role in [role.id for role in interaction.user.roles]:
            return True
        else:
            return False

    @discord.ui.button(label="", emoji="⏸️", style=discord.ButtonStyle.primary)
    async def pause(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.check_roles(interaction, int(env[3])):
            return await interaction.response.edit_message(view=self)

        current_voice = self.get_current_voice(interaction)

        if current_voice is not None:
            if not current_voice.is_paused():
                self.reset_buttons()
                current_voice.pause()
                button.style = discord.ButtonStyle.success
                return await interaction.response.edit_message(view=self)

        return await interaction.response.edit_message(view=self)

    @discord.ui.button(label="", emoji="▶️", style=discord.ButtonStyle.success)
    async def play(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.check_roles(interaction, int(env[3])):
            return await interaction.response.edit_message(view=self)

        current_voice = self.get_current_voice(interaction)

        if current_voice is not None:
            if current_voice.is_paused():
                self.reset_buttons()
                current_voice.resume()
                button.style = discord.ButtonStyle.success
                return await interaction.response.edit_message(view=self)

        return await interaction.response.edit_message(view=self)

    @discord.ui.button(label="", emoji="⏭️", style=discord.ButtonStyle.primary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.check_roles(interaction, int(env[3])):
            return await interaction.response.edit_message(view=self)

        current_voice = self.get_current_voice(interaction)

        if current_voice is not None:
            current_voice.stop()

        return await interaction.response.edit_message(view=self)

    @discord.ui.button(label="", emoji="⏹️", style=discord.ButtonStyle.primary)
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.check_roles(interaction, int(env[3])):
            return await interaction.response.edit_message(view=self)

        current_voice = self.get_current_voice(interaction)

        if current_voice is not None:
            current_voice.stop()

            cog = self.bot.get_cog("fm")
            await cog.players[interaction.guild.id].clear_queue()
            del cog.players[interaction.guild.id]

        return await interaction.message.delete()

class VoiceCog(commands.GroupCog, name="fm"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.players = {}
        self.endless = False
        super().__init__()

    def get_player(self, ctx):
        try:
            player = self.players[ctx.guild.id]
        except KeyError:
            player = Player(ctx, self.bot)
            self.players[ctx.guild.id] = player

        return player

    @app_commands.command(name="join", description="Join a voice channel.")
    @app_commands.describe(channel="the voice channel to join, or blank for the channel you're in")
    async def join(self, interaction: discord.Interaction, channel: discord.VoiceChannel=None):
        if not channel:
            if interaction.user.voice:
                try:
                    channel = interaction.user.voice.channel
                except AttributeError:
                    return await interaction.response.send_message("No channel found. Please join a voice channel or specify a valid one.", ephemeral=True)
            else:
                return await interaction.response.send_message("No channel found. Please join a voice channel or specify a valid one.", ephemeral=True)

        if interaction.guild.voice_client in self.bot.voice_clients:
            if interaction.guild.voice_client.channel == channel:
                return
            else:
                await interaction.guild.voice_client.move_to(channel)
                return await interaction.response.send_message(f"Moved to channel {channel}!", ephemeral=True)
        else:
            await channel.connect()
            return await interaction.response.send_message(f"Joined channel {channel}!", ephemeral=True)

    @app_commands.command(name="leave", description="Leave the current voice channel.")
    async def leave(self, interaction: discord.Interaction):
        # TODO: cleanup before leaving if bot is currently playing
        # TODO: check if bot is in voice before DCing
        await interaction.guild.voice_client.disconnect()
        return await interaction.response.send_message("Left voice!")

    @app_commands.command(name="begin", description="Start playing scheduled playlists.")
    async def begin(self, interaction: discord.Interaction):
        if not interaction.guild.voice_client:
            # TODO: maybe just have the bot join the voice channel?
            return await interaction.response.send_message("I am not currently connected to a voice channel!", ephemeral=True)
        else:
            player = self.get_player(interaction)
            await player.check_schedule(override=True)
            return await interaction.response.send_message("TeaL FM has started.", ephemeral=True)
    
    @app_commands.command(name="play", description="Play a downloaded playlist.")
    @app_commands.describe(target="the playlist you want to play", shuffle="whether or not to shuffle the playlist", endless="whether you want the playlist to loop")
    async def play(self, interaction: discord.Interaction, target: str, shuffle: bool, endless: bool = False):
        if not interaction.guild.voice_client:
            # TODO: maybe just have the bot join the voice channel?
            return await interaction.response.send_message("I am not currently connected to a voice channel!", ephemeral=True)

        try:
            player = self.get_player(interaction)
            player.is_scheduled = False
            await player.start_playlist(target, interaction, shuffle, endless)

            return await interaction.response.send_message("Started playing!", ephemeral=True)
        except Exception as e:
            await interaction.channel.send(f"Something went wrong. Error: `{e}`")

    @play.autocomplete('target')
    async def target_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> List[app_commands.Choice[str]]:
        return [
            app_commands.Choice(name=dir, value=dir) for dir in playlist_dirs if current.lower() in dir.lower()
        ]

class PlaylistCog(commands.GroupCog, name="playlist"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        super().__init__()

    @commands.command()
    async def sync(self, message):
        try:
            for guild in env[4:]:
                await self.bot.tree.sync(guild=discord.Object(id=guild))
        except Exception as e:
            await message.send(f"Something went wrong while syncing commands. Error: `{e}`")
        else:
            await message.send("Commands have been synced!")

    @app_commands.command(name="list", description="List downloaded playlists.")
    async def list(self, interaction: discord.Interaction) -> None:
        update_playlist_dirs()

        if len(playlist_dirs) == 0:
            return await interaction.response.send_message("I have no playlists downloaded!")

        output_string = "Downloaded playlists:\n \n"

        for dir in playlist_dirs:
            output_string += "~ " + dir + "\n"

        return await interaction.response.send_message(output_string)

    def write_output_files(self, playlist_name: str, metadata: dict, failed_songs_output: str):
        metadata_file = open(f"./playlists/{playlist_name}/metadata.json", "w")
        json.dump(metadata, metadata_file)
        metadata_file.close()
        if failed_songs_output != "":
            fail_file = open(f"./playlists/{playlist_name}/failed_songs.txt", "w") # TODO: will need to change this when adding modular playlist updating
            fail_file.write(failed_songs_output)
            fail_file.close()

    @app_commands.command(name="download", description="Download a Spotify playlist.")
    @app_commands.describe(url="the URL of the playlist you want to download")
    async def download(self, interaction: discord.Interaction, url: str) -> None:
        if not (("http" in url) and ("open.spotify.com/playlist" in url)):
            return await interaction.response.send_message("I didn't recognize your link; please provide me with a valid URL/URI to a Spotify playlist.", ephemeral=True)

        send_message = await interaction.response.send_message(f"Link received. Parsing playlist data...", ephemeral=True)

        if not os.path.exists("./playlists"):
            os.mkdir("./playlists")

        LIMIT = 100
        try:
            playlist_tracks = []
            playlist_data = spotify_client.playlist_tracks(url, limit=LIMIT)
            playlist_name = spotify_client.playlist(url)["name"]

            # We have to use this offset structure
            # because we can't grab more than 100
            # tracks from the playlist at once.
            index = 0
            while playlist_data['next'] is not None:
                for item in playlist_data["items"]:
                    playlist_tracks.append(item)
                index += LIMIT
                playlist_data = spotify_client.playlist_tracks(url, offset=index, limit=LIMIT)
            if playlist_data["items"] != []:
                for item in playlist_data["items"]:
                    playlist_tracks.append(item)

            songs = []
            metadata = {}

            # TODO: sanitize playlist name to make sure
            # directory can be successfully created
            # TODO: handle prior failed downloads
            if(os.path.exists(f"./playlists/{playlist_name}")):
                # Note that two playlists could have the same name,
                # so we should confirm that the playlists have the
                # same content/UUID before updating the downloads
                await interaction.edit_original_message(content="Playlist previously downloaded. Updating...")
                if os.path.exists(f"./playlists/{playlist_name}/metadata.json"):
                    metadata_file = open(f"./playlists/{playlist_name}/metadata.json", "r")
                    metadata = json.load(metadata_file)
                else:
                    metadata["tracks"] = {}
                    for index, item in enumerate(playlist_tracks):
                        artist_text = ""
                        for artist in item["track"]["artists"]:
                            artist_text += artist["name"] + ", "
                        artist_text = artist_text[:-2]

                        if f"{artist_text} - {item['track']['name']}.mp3" in os.listdir(f"./playlists/{playlist_name}"):
                            metadata[artist_text + " - " + item["track"]["name"]] = {"album_art": item["track"]["album"]["images"][0]["url"], "url": item["track"]["external_urls"]["spotify"], "download_succeeded": True} # Probably would be best to use a UUID for this, but it should work for now
                            metadata["tracks"][index] = artist_text + " - " + item["track"]["name"]
                    metadata_file = open(f"./playlists/{playlist_name}/metadata.json", "w")
                    json.dump(metadata, metadata_file)
                    metadata_file.close()

                # TODO: fix
                # directory_contents = os.listdir(f"./playlists/{playlist_name}")
                # for item in directory_contents:
                #     if item not in [".spotdl-cache", "failed_songs.txt", "metadata.json"]:
                #         if item[:-4] not in metadata:
                #             os.remove(f"./playlists/{playlist_name}/{item}")

            else:
                os.mkdir(f"./playlists/{playlist_name}")
                metadata["tracks"] = {}

            DEFAULT_ALBUM_ART = "http://wiki.theplaz.com/w/images/Windows_Media_Player_12_Default_Album_Art.png"
            for index, item in enumerate(playlist_tracks):
                try:
                    artist_text = ""
                    for artist in item["track"]["artists"]:
                        artist_text += artist["name"] + ", "
                    artist_text = artist_text[:-2]

                    if len(metadata) != 1:
                        if f"{artist_text} - {item['track']['name']}" in metadata:
                            continue

                    album_art_link = ""
                    if len(item["track"]["album"]["images"]) > 0:
                        album_art_link = item["track"]["album"]["images"][0]["url"]
                    else:
                        album_art_link = DEFAULT_ALBUM_ART
                    #               track name            artist(s)          spotify link                      album image url   track number
                    songs.append([item["track"]["name"], artist_text, item["track"]["external_urls"]["spotify"], album_art_link, index])
                    # Track ordering will likely break if playlist order is changed and then updated. Not sure how important that is...
                    # TODO: could just update metadata of songs that have a pre-existing download to fix the above problem
                except Exception as e:
                    # Needed to make sure all songs in playlist
                    # can be accessed, skipping over those that
                    # might be missing required information
                    pass

            if len(songs) == 0:
                return await interaction.edit_original_message(content="Playlist is up to date!")

            output_string = f"Downloading {len(songs)} songs from *{playlist_name}*... \n \n"
            if len(songs) < LIMIT:
                for song in songs:
                    output_string += f":arrow_down: {song[0]} - {song[1]}\n"
            else:
                output_string += f"(0/{len(songs)})\n"
                output_string += f":arrow_down: {songs[0][0]} - {songs[0][1]}\n"
                output_string += f":arrow_down: {songs[1][0]} - {songs[1][1]}\n"

            await interaction.edit_original_message(content="Download started.")
            output_message = await interaction.channel.send(content=output_string)

            text_array = output_string.split("\n")
            failed_songs_count = 0
            failed_songs_output = ""

            for index in range(len(songs)):
                await interaction.channel.typing()
                run_return = subprocess.run(["spotdl", "-o", f"./playlists/{playlist_name}", songs[index][2]], capture_output=True, text=True) # TODO: grab m3u to keep track of playlist data
                if run_return.returncode == 0:
                    if len(songs) < LIMIT:
                        text_array[index + 2] = f":white_check_mark: {songs[index][0]} - {songs[index][1]}"
                    else:
                        text_array[2] = f"({index+1}/{len(songs)})"
                        text_array[3] = f":white_check_mark: {songs[index][0]} - {songs[index][1]}"
                        text_array[4] = (f":arrow_down: {songs[index+1][0]} - {songs[index+1][1]}" if index < len(songs)-1 else "")

                    updated_msg = ""
                    for line in text_array:
                        updated_msg += line + "\n"

                    # TODO: need to sanitize the song names/artists,
                    # as sometimes they might contain special characters
                    # that are not valid in file names and cause KeyErrors
                    metadata[songs[index][1] + " - " + songs[index][0]] = {"album_art": songs[index][3], "url": songs[index][2], "download_succeeded": True} # Probably would be best to use a UUID for this, but it should work for now
                    metadata["tracks"][songs[index][4]] = songs[index][1] + " - " + songs[index][0]
                    output_message = await output_message.edit(content=updated_msg)
                else:
                    if len(songs) < LIMIT:
                        text_array[index + 2] = f":x: {songs[index][0]} - {songs[index][1]}"
                    else:
                        text_array[2] = f"({index+1}/{len(songs)})"
                        text_array[3] = f":x: {songs[index][0]} - {songs[index][1]}"
                        text_array[4] = (f":arrow_down: {songs[index+1][0]} - {songs[index+1][1]}" if index < len(songs)-1 else "")

                    metadata[songs[index][1] + " - " + songs[index][0]] = {"album_art": songs[index][3], "url": songs[index][2], "download_succeeded": False} # Probably would be best to use a UUID for this, but it should work for now

                    updated_msg = ""
                    for line in text_array:
                        updated_msg += line + "\n"

                    failed_songs_output += f"Downloading of {songs[index][0]} - {songs[index][1]} failed with error code {run_return.returncode}. \n stdout: \n {run_return.stdout} \n\n stderr: \n {run_return.stderr} \n\n"
                    failed_songs_count += 1

                    output_message = await output_message.edit(content=updated_msg)

            # TODO: should probably do this after each
            # failed download so that in the event of a crash,
            # the information can still be retrieved
            self.write_output_files(playlist_name, metadata, failed_songs_output)
            await interaction.channel.send(f"Done! Downloaded {len(songs) - failed_songs_count} songs. {(f'{failed_songs_count} songs failed to be downloaded. Please check error logs for more information.') if failed_songs_count != 0 else ''}")

            update_playlist_dirs()

        except Exception as e:
            # Write output files anyways just in
            # case we want to use the playlist.
            self.write_output_files(playlist_name, metadata, failed_songs_output)
            update_playlist_dirs()

            await interaction.channel.send(f"Something went wrong. Error: `{e}`")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PlaylistCog(bot), guilds=[discord.Object(id=guild) for guild in env[4:]])
    await bot.add_cog(VoiceCog(bot), guilds=[discord.Object(id=guild) for guild in env[4:]])

######################################################

async def main():
    intents = discord.Intents.default()
    intents.message_content = True
    bot = commands.Bot(command_prefix="!", description="Bot is in testing!", intents=intents)

    update_playlist_dirs()

    async with bot:
        @bot.event
        async def on_ready():
            print("~~~~~~~~~~~")
            print("Logged in as")
            print(bot.user.name)
            print(bot.user.id)
            print("~~~~~~~~~~~")

        await setup(bot)
        await bot.start(env[0].strip())

asyncio.run(main())
