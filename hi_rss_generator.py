import click
import collections
import concurrent.futures
import pathlib
import queue
import requests
import time
from bs4 import BeautifulSoup
from podgen import Podcast, Episode, Media, Person, Category
from requests.adapters import HTTPAdapter, Retry

# Declare a namedtuple to store episode metadata during initial scrape
EpisodeInfo = collections.namedtuple("EpisodeInfo", ["index", "title", "url"])

# Define a queue that will hold the episodes to iterate through
episode_queue = queue.Queue()

# Define a retry strategy to use for http requests
retry_strategy = Retry(
    total=10,  # Maximum number of retries
    backoff_factor=2,  # Exponential backoff factor (e.g., 2 means 1, 2, 4, 8 seconds, ...)
    status_forcelist=[429, 500, 502, 503, 504],  # HTTP status codes to retry on
)

# Create an HTTP adapter with the retry strategy defined above
http_adapter = HTTPAdapter(max_retries=retry_strategy)

# Create a new session object and mount the http_adapter
request_session = requests.Session()
request_session.mount('http://', http_adapter)
request_session.mount('https://', http_adapter)

def get_page_from_url(url):
    """
    This function simply uses "requests" to get the contents of a given url,
    using the request_session with the predefined retry_strategy; in case of
    error it returns None.
    """

    try:
        page = request_session.get(url, headers={"User-agent": "your bot 0.1"})
        page.raise_for_status()
    except Exception as e:
        print(f"Error occured while getting url: {url}:")
        print(e)
    else:
        # No exception occurred, return page object
        return page

    return None


def get_episodes(start=1, end=None):
    """
    This function scrapes the main "Hello Internet" website starting from episode
    `start`, WHICH NEEDS TO BE VALID! Some episodes don't have a regular index
    number, so they can't be used to start the scrape. The very first episode (1),
    however, is valid, that's why it's the default.
    """

    episode_index = start
    base_url = f"https://www.hellointernet.fm"
    episode_url = f"https://www.hellointernet.fm/podcast/{episode_index}"

    while (
        episode_index <= end if end else episode_url != None
    ) and episode_url != None:

        episode_page = get_page_from_url(episode_url)

        if episode_page.status_code != 200:
            print(
                f"Episode {episode_index}: page_status_code = {episode_page.status_code}"
            )
            print(
                f"Episode {episode_index}: page_status_reason = {episode_page.reason}"
            )

        soup = BeautifulSoup(episode_page.content, "html.parser")
        episode_title = soup.find("h1", class_="entry-title").text

        episode_info = EpisodeInfo(episode_index, episode_title, episode_url)
        episode_queue.put(episode_info)

        print(f'Added episode {episode_index}: "{episode_title}" to the queue')

        next_episode = soup.find("a", id="prevLink")
        episode_index += 1
        episode_url = base_url + next_episode["href"] if next_episode else None

    print(
        f"Finished scrapping, submitted {episode_index - start} episodes for processing"
    )


def generate_episode(episode_info, media_location=None):
    """
    This function scrapes a specific url, gathers all the necessary information and
    generates a podgen episode object with it.
    """

    print(
        f'Generating episode {episode_info.index}: "{episode_info.title}" ({episode_info.url})'
    )

    episode_page = get_page_from_url(episode_info.url)

    if episode_page.status_code != 200:
        print(
            f"Episode {episode_info.index}: page_status_code = {episode_page.status_code}"
        )
        print(
            f"Episode {episode_info.index}: page_status_reason = {episode_page.reason}"
        )

    soup = BeautifulSoup(episode_page.content, "html.parser")
    episode_author = soup.find("meta", itemprop="author")["content"]
    episode_link = soup.find("meta", itemprop="url")["content"]
    episode_datePublished = soup.find("meta", itemprop="datePublished")["content"]

    # Find the top-level container
    body_content = soup.find(
        "div", class_="body entry-content"
    )

    # Within the body_content, find ALL instances of "sqs-block-content",
    # which are the individual meaningful content blocks.
    blocks = body_content.find_all(
        "div", class_="sqs-block-content"
    )

    # Initialize the episode_content string; we will append the content of each
    # content block within body_content.
    episode_content = ""

    # Traverse each block and extract the actual content within
    for block in blocks:
      # Skip the audio block from the episode_content
      if block.find("div", class_="sqs-audio-embed"):
        continue
      # If there is a "noscript" node within the current block, add a set of
      # <p></p> tags around its content and append it to episode_content.
      noscript_block = block.find("noscript")
      if noscript_block:
        episode_content += f"<p>{noscript_block.decode_contents()}</p>"
      else:
        # Grab the actual content using the "undocumented" decode_contents() method,
        # which returns ONLY the data inside the "div" block, which is what we want.
        episode_content += block.decode_contents()

    # Now get the audio object information
    sqs_audio_embed_object = soup.find("div", class_="sqs-audio-embed").attrs
    episode_media_url = sqs_audio_embed_object["data-url"]

    # Let podgen get the episode media information from the server
    episode_media = Media.create_from_server_response(episode_media_url)

    # Now create the actual podgen episode object and populate the fields
    episode = Episode()
    episode.title = episode_info.title
    episode.authors = [Person(f"{episode_author}")]
    episode.link = episode_link
    episode.summary = episode_content
    episode.publication_date = episode_datePublished
    episode.media = episode_media

    if media_location:
        # If "media_location" is set, use it to store the actual media files
        file_name = (
            f"hello_internet_{episode_info.index:03d}{episode_media.file_extension}"
        )
        file_path = pathlib.Path(media_location) / file_name
        episode.media.download(file_path)
        # Use populate_duration_from to get the information from the downloaded file
        episode.media.populate_duration_from(file_path)
    else:
        # Use fetch_duration which will download the media file to a temp location and get the information from it
        episode.media.fetch_duration()

    return episode


# Create the Podcast object
podcast = Podcast(
    name="Hello Internet (archive)",
    authors=[Person("CGP Grey"), Person("Brady Haran")],
    description="CGP Grey and Brady Haran talk about YouTube, life, work, whatever.",
    subtitle="CGP Grey and Brady Haran in Conversation.",
    website="http://www.hellointernet.fm/",
    image="https://images.squarespace-cdn.com/content/v1/52d66949e4b0a8cec3bcdd46/1391195775824-JVU9K0BX50LWOKG99BL5/Hello+Internet.003.png",
    category=Category("Education"),
    language="en-US",
    explicit=False,
)


def validate_keep_media_path(ctx, param, value):
    """
    Validate the path used to set the 'keep_media' option
    """
    if value is None or pathlib.Path(value).exists():
        return value
    else:
        path = pathlib.Path(value)
        pathlib.Path(path).mkdir(parents=True, exist_ok=True)
        if path.exists():
            return path
        else:
            raise click.BadParameter("provide a valid path for media")


@click.command()
@click.option(
    "-o",
    "--out",
    "rss_file",
    type=str,
    default="rss.xml",
    required=False,
)
@click.option(
    "-m",
    "--max_workers",
    "max_workers",
    type=int,
    default=20,
    required=False,
    help="number of worker threads to use (20 by default)",
)
@click.option(
    "-f",
    "--first",
    "first_episode_index",
    type=int,
    default=1,
    required=False,
    help="index of first episode to parse (needs to resolve to valid url)",
)
@click.option(
    "-l",
    "--last",
    "last_episode_index",
    type=int,
    default=None,
    required=False,
    help="index of last episode to parse",
)
@click.option(
    "-k",
    "--keep_media",
    "media_location",
    type=str,
    prompt=True,
    prompt_required=False,
    required=False,
    callback=validate_keep_media_path,
    help="if used, media files will be kept in the directory given",
)
def main(
    rss_file, max_workers, first_episode_index, last_episode_index, media_location
):
    """
    Main function
    """

    print(f"Using the following arguments: ")
    print(f"\trss_file = {rss_file}")
    print(f"\tmax_workers = {max_workers}")
    print(f"\tfirst_episode_index = {first_episode_index}")
    print(f"\tlast_episode_index = {last_episode_index}")
    print(f"\tmedia_location = {media_location}")
    processed_episodes = []
    failed_episodes = []

    # Grab the start time snapshot
    start = time.time()

    # We can use a "with" statement to ensure threads are cleaned up promptly
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:

        # Start the producer thread which sends work in through the queue
        future_to_episode = {
            executor.submit(
                get_episodes, first_episode_index, last_episode_index
            ): "PRODUCER"
        }

        while future_to_episode:
            # check for status of the futures which are currently working
            done, not_done = concurrent.futures.wait(
                future_to_episode,
                timeout=1,
                return_when=concurrent.futures.FIRST_COMPLETED,
            )

            # If there is incoming work, start a new future
            while not episode_queue.empty():

                # Grab an episode_info object from the queue
                episode_info = episode_queue.get()

                # Create a new future with the episode_info object
                future_to_episode[
                    executor.submit(generate_episode, episode_info, media_location)
                ] = episode_info

            # Process any completed futures
            for future in done:
                episode_info = future_to_episode[future]
                try:
                    episode_object = future.result()
                except Exception as e:
                    if isinstance(episode_info, EpisodeInfo):
                        print(
                            f"Episode {episode_info.index} generated an exception: {e}"
                        )
                        failed_episodes.append((episode_info, e))
                    elif isinstance(episode_info, str):
                        raise Exception("Producer thread generated an exception") from e
                    else:
                        raise Exception(f"Unexpected exception") from e
                else:
                    if isinstance(episode_object, Episode):
                        print(f'Finished processing episode "{episode_object.title}"')
                        processed_episodes.append((episode_info, episode_object))

                # Remove the now completed future
                future_to_episode.pop(future)

    # Since the episodes may have been processed out of order, sort them using the
    # episode index; the lambda allows us to do it in one shot.
    processed_episodes.sort(key=lambda episode: episode[0].index)

    # Add the episodes to the podcast object, iterate in reverse to add latest
    # episodes at the top of the feed.
    for episode in reversed(processed_episodes):
        podcast.episodes.append(episode[1])

    # Generate the actuall rss feed file
    podcast.rss_file(rss_file, minimize=False)

    # Grab the end time snapshot
    end = time.time()

    # Log the results
    print(f"Generated RSS feed ({rss_file}) in {(end - start):.2f} seconds")

    print(
        f"{len(processed_episodes)} episodes were succesfully processed:\n"
        + "\n".join(
            "\tEpisode {index}: {title}".format(**episode[0]._asdict())
            for episode in processed_episodes
        )
    ) if len(processed_episodes) > 0 else None

    print(
        f"{len(failed_episodes)} episodes failed to be processed:\n"
        + "\n".join(
            "\tEpisode {index}: {title}".format(**episode[0]._asdict())
            for episode in failed_episodes
        )
    ) if len(failed_episodes) > 0 else None


if __name__ == "__main__":
    main()
