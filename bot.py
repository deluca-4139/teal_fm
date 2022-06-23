import discord
from discord import app_commands
from discord.app_commands import Choice
from discord.ext import commands

import spotipy
from spotipy import SpotifyClientCredentials

import asyncio, subprocess, os, random, json
from async_timeout import timeout
from typing import List

env = []
with open("env", "r") as infile:
    for line in infile:
        env.append(str(line).strip())

client_creds = SpotifyClientCredentials(client_id=env[1], client_secret=env[2])
spotify_client = spotipy.Spotify(client_credentials_manager=client_creds)

playlist_dirs = []

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

        self.volume = 0.5
        self.song_list = []
        self.song_paths = []

        self.album_image_links = {}

        # This will likely break if you switch to 3.10 in the future
        bot.loop.create_task(self.player_loop())

    async def player_loop(self):
        await self.bot.wait_until_ready()

        while not self.bot.is_closed():
            self.next.clear()

            try:
                async with timeout(300):
                    source = await self.queue.get()
            except asyncio.TimeoutError:
                # TODO: implement timeout disconnect
                pass

            source.volume = self.volume

            playing_embed = discord.Embed(title="Now Playing", description=self.song_list[0])
            playing_embed.set_footer(text="Up next: {}".format(self.song_list[1] if (len(self.song_list) > 1) else "nothing"))
            playing_embed.set_image(url=self.album_image_links[self.song_list[0]])
            await self.ctx.edit_original_message(content="", embed=playing_embed, view=PlayerButtons(self.bot))

            self.guild.voice_client.play(source, after=lambda _: self.bot.loop.call_soon_threadsafe(self.next.set))
            await self.next.wait()

            last_played = self.song_list.pop(0)
            last_path = self.song_paths.pop(0)
            if self.endless:
                self.song_list.append(last_played)
                self.song_paths.append(last_path)
                await self.queue.put(discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(last_path)))
            await self.ctx.edit_original_message(content="", embed=None) # Embed might not update properly if we don't clear it first

            # TODO: fix? maybe unnecessary?
            try:
                source.cleanup()
            except ValueError:
                pass

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

            cog = self.bot.get_cog("voice")
            # We need to confirm this value is false,
            # otherwise the queue will never be empty
            cog.players[interaction.guild.id].endless = False

            while not cog.players[interaction.guild.id].queue.empty():
                await cog.players[interaction.guild.id].queue.get()
            del cog.players[interaction.guild.id]

            for child in self.children:
                child.disabled = True

        return await interaction.response.edit_message(view=self)

class VoiceCog(commands.GroupCog, name="voice"):
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

    @app_commands.command(name="join")
    async def join(self, interaction: discord.Interaction, channel: discord.VoiceChannel=None):
        if not channel:
            if interaction.user.voice:
                try:
                    channel = interaction.user.voice.channel
                except AttributeError:
                    return await interaction.response.send_message("No channel found. Please join a voice channel or specify a valid one.")
            else:
                return await interaction.response.send_message("No channel found. Please join a voice channel or specify a valid one.")

        if interaction.guild.voice_client in self.bot.voice_clients:
            if interaction.guild.voice_client.channel == channel:
                return
            else:
                await interaction.guild.voice_client.move_to(channel)
                return await interaction.response.send_message(f"Moved to channel {channel}!")
        else:
            await channel.connect()
            return await interaction.response.send_message(f"Joined channel {channel}!")

    @app_commands.command(name="leave")
    async def leave(self, interaction: discord.Interaction):
        # TODO: cleanup before leaving if bot is currently playing
        # TODO: check if bot is in voice before DCing
        await interaction.guild.voice_client.disconnect()
        return await interaction.response.send_message("Left voice!")

    @app_commands.command(name="play")
    async def play(self, interaction: discord.Interaction, target: str, shuffle: bool, endless: bool = False):
        if not interaction.guild.voice_client:
            # TODO: maybe just have the bot join the voice channel?
            return await interaction.response.send_message("I am not currently connected to a voice channel!")

        try:
            player = self.get_player(interaction)
            player.ctx = interaction
            player.endless = endless

            song_list = os.listdir(f"./playlists/{target}")
            if shuffle:
                random.shuffle(song_list)

            paths = []
            for song in song_list:
                if song not in [".spotdl-cache", "album_image_links.json", "failed_songs.txt"]:
                    source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(f"./playlists/{target}/{song}"))
                    await player.queue.put(source)
                    paths.append(f"./playlists/{target}/{song}")

                    index = len(song) - 1
                    while song[index] != '.':
                        index -= 1
                    player.song_list.append(song[0:index])
            player.song_paths = paths

            links_file = open(f"./playlists/{target}/album_image_links.json", "r")
            player.album_image_links = json.load(links_file)
            links_file.close()

            return await interaction.response.send_message("Attempting to play...")
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
            await self.bot.tree.sync(guild=discord.Object(id=514188804433641472))
        except:
            await message.send("Something went wrong while syncing commands.")
        else:
            await message.send("Commands have been synced!")

    @app_commands.command(name="list")
    async def list(self, interaction: discord.Interaction) -> None:
        update_playlist_dirs()

        if len(playlist_dirs) == 0:
            return await interaction.response.send_message("I have no playlists downloaded!")

        output_string = "Downloaded playlists:\n \n"

        for dir in playlist_dirs:
            output_string += "~ " + dir + "\n"

        return await interaction.response.send_message(output_string)

    @app_commands.command(name="download")
    async def download(self, interaction: discord.Interaction, url: str) -> None:
        if not (("http" in url) and ("open.spotify.com/playlist" in url)):
            return await interaction.response.send_message("I didn't recognize your link; please provide me with a valid URL/URI to a Spotify playlist.")

        send_message = await interaction.response.send_message(f"Link received. Parsing playlist data...")

        if not os.path.exists("./playlists"):
            os.mkdir("./playlists")

        try:
            playlist_data = spotify_client.playlist_tracks(url)
            playlist_name = spotify_client.playlist(url)["name"]

            # TODO: sanitize playlist name to make sure
            # directory can be successfully created
            if(os.path.exists(f"./playlists/{playlist_name}")):
                # TODO: check link for updated playlist info
                # Note that two playlists could have the same name,
                # so we should confirm that the playlists have the
                # same content/UUID before updating the downloads
                return await interaction.edit_original_message(content="I already have a directory with that name! Have you previously downloaded this playlist?")
            os.mkdir(f"./playlists/{playlist_name}")

            songs = []
            for item in playlist_data["items"]:
                artist_text = ""
                for artist in item["track"]["artists"]:
                    artist_text += artist["name"] + ", "
                artist_text = artist_text[:-2]

                #               track name            artist(s)          spotify link                               album image url
                songs.append([item["track"]["name"], artist_text, item["track"]["external_urls"]["spotify"], item["track"]["album"]["images"][0]["url"]])

            output_string = f"Downloading songs from *{playlist_name}*... \n \n"
            for song in songs:
                output_string += ":arrow_down: " + song[0] + " - " + song[1] + "\n"

            await interaction.edit_original_message(content=output_string)

            album_image_links = {}
            text_array = output_string.split("\n")
            failed_songs_output = ""

            for index, song in enumerate(songs):
                await interaction.channel.typing()
                run_return = subprocess.run(["spotdl", "-o", f"./playlists/{playlist_name}", song[2]], capture_output=True, text=True) # TODO: grab m3u to keep track of playlist data
                if run_return.returncode == 0:
                    text_array[index + 2] = ":white_check_mark: " + song[0] + " - " + song[1]
                    updated_msg = ""
                    for line in text_array:
                        updated_msg += line + "\n"

                    album_image_links[song[1] + " - " + song[0]] = song[3] # Probably would be best to use a UUID for this, but it should work for now
                    await interaction.edit_original_message(content=updated_msg)
                else:
                    text_array[index + 2] = ":x: " + song[0] + " - " + song[1]
                    updated_msg = ""
                    for line in text_array:
                        updated_msg += line + "\n"

                    failed_songs_output += f"Downloading of {song[0]} - {song[1]} failed with error code {run_return.returncode}. \n stdout: \n {run_return.stdout} \n\n stderr: \n {run_return.stderr} \n\n"

                    await interaction.edit_original_message(content=updated_msg)

            links_file = open(f"./playlists/{playlist_name}/album_image_links.json", "w")
            json.dump(album_image_links, links_file)
            links_file.close()
            await interaction.channel.send("Done!")

            if failed_songs_output != "":
                fail_file = open(f"./playlists/{playlist_name}/failed_songs.txt", "w") # TODO: will need to change this when adding modular playlist updating
                fail_file.write(failed_songs_output)
                fail_file.close()
                await interaction.channel.send("Some songs were unable to be downloaded. Please check error logs for more information.")

            update_playlist_dirs()

        except Exception as e:
            await interaction.channel.send(f"Something went wrong. Error: `{e}`")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PlaylistCog(bot), guild=discord.Object(id=514188804433641472))
    await bot.add_cog(VoiceCog(bot), guild=discord.Object(id=514188804433641472))

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
