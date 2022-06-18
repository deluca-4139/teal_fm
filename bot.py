import discord
from discord import app_commands
from discord.ext import commands

import spotipy
from spotipy import SpotifyClientCredentials

import asyncio, subprocess, os, random
from async_timeout import timeout

env = []
with open("env", "r") as infile:
    for line in infile:
        env.append(str(line).strip())

client_creds = SpotifyClientCredentials(client_id=env[1], client_secret=env[2])
spotify_client = spotipy.Spotify(client_credentials_manager=client_creds)

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

        self.queue = asyncio.Queue()
        self.next = asyncio.Event()

        self.now_playing = None
        self.volume = 0.5
        self.current = None
        self.song_list = []

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
            self.current = source

            self.guild.voice_client.play(source, after=lambda _: self.bot.loop.call_soon_threadsafe(self.next.set))
            self.now_playing = await self.channel.send(f"**~NOW PLAYING~** \n \n {self.song_list[0]}")
            await self.next.wait()

            self.song_list.pop(0)

            source.cleanup()
            self.current = None
            try:
                await self.now_playing.delete()
            except discord.HTTPException:
                pass


class VoiceCog(commands.GroupCog, name="voice"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.players = {}
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
            if interaction.user.voice.channel:
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
        await interaction.guild.voice_client.disconnect()
        return await interaction.response.send_message("Left voice!")

    @app_commands.command(name="play")
    async def play(self, interaction: discord.Interaction, target: str, shuffle: bool):
        if not interaction.guild.voice_client:
            # TODO: maybe just have the bot join the voice channel?
            return await interaction.response.send_message("I am not currently connected to a voice channel!")

        try:
            player = self.get_player(interaction)

            song_list = os.listdir(f"./playlists/{target}")
            if shuffle:
                random.shuffle(song_list)

            for song in song_list:
                if song != ".spotdl-cache":
                    source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(f"./playlists/{target}/{song}"))
                    await player.queue.put(source)

                    index = len(song) - 1
                    while song[index] != '.':
                        index -= 1
                    player.song_list.append(song[0:index])

            return await interaction.response.send_message("Attempting to play...")
        except Exception as e:
            await interaction.channel.send(f"Something went wrong. Error: `{e}`")

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
        if not os.path.exists("./playlists"):
            return await interaction.response.send_message("I have no playlists downloaded!")

        if len(os.listdir("./playlists")) == 0:
            return await interaction.response.send_message("I have no playlists downloaded!")

        output_string = "Downloaded playlists:\n \n"

        for entry in os.listdir("./playlists"):
            output_string += "~ " + entry + "\n"

        return await interaction.response.send_message(output_string)

    @app_commands.command(name="download")
    async def download(self, interaction: discord.Interaction, url: str) -> None:
        # TODO: parse URL to make sure it is a valid spotify playlist
        await interaction.response.send_message(f"Link received. Parsing playlist data...")

        if not os.path.exists("./playlists"):
            os.mkdir("./playlists")

        try:
            playlist_data = spotify_client.playlist_tracks(url)
            playlist_name = spotify_client.playlist(url)["name"]

            if(os.path.exists(f"./playlists/{playlist_name}")):
                return await interaction.channel.send("I already have a directory with that name! Have you previously downloaded this playlist?")
            os.mkdir(f"./playlists/{playlist_name}")

            songs = []
            for item in playlist_data["items"]:
                artist_text = ""
                for artist in item["track"]["artists"]:
                    artist_text += artist["name"] + ", "
                artist_text = artist_text[:-2]

                songs.append([item["track"]["name"], artist_text, item["track"]["external_urls"]["spotify"]])

            output_string = f"Downloading songs from *{playlist_name}*... \n \n"
            for song in songs:
                output_string += ":arrow_down: " + song[0] + " - " + song[1] + "\n"

            send_message = await interaction.channel.send(output_string)

            for index, song in enumerate(songs):
                await interaction.channel.typing()
                return_val = subprocess.call(["spotdl", "-o", f"./playlists/{playlist_name}", song[2]])
                if return_val == 0:
                    text_array = send_message.content.split("\n")
                    text_array[index + 2] = ":white_check_mark: " + song[0] + " - " + song[1]
                    updated_msg = ""
                    for line in text_array:
                        updated_msg += line + "\n"
                    send_message = await send_message.edit(content=updated_msg)

            await interaction.channel.send("Done!")

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
