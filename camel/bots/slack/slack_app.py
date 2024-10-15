# =========== Copyright 2023 @ CAMEL-AI.org. All Rights Reserved. ===========
# Licensed under the Apache License, Version 2.0 (the “License”);
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an “AS IS” BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# =========== Copyright 2023 @ CAMEL-AI.org. All Rights Reserved. ===========
import logging
import os
import threading
from typing import TYPE_CHECKING, Optional

from slack_sdk.oauth.installation_store.async_installation_store import (
    AsyncInstallationStore,
)
from starlette.requests import Request
from starlette.responses import Response

from camel.bots.slack.models import (
    SlackEventBody,
    SlackEventProfile,
)
from camel.utils import dependencies_required

if TYPE_CHECKING:
    from slack_bolt.context.async_context import AsyncBoltContext
    from slack_bolt.context.say.async_say import AsyncSay
    from slack_sdk.web.async_client import AsyncWebClient

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


class SlackApp:
    r"""Represents a Slack app that is powered by a Slack Bolt `AsyncApp`.

    This class is responsible for initializing and managing the Slack
    application by setting up event handlers, running the app server, and
    handling events such as messages and mentions from Slack.

    Args:
        token (Optional[str]): Slack API token for authentication.
        scopes (Optional[str]): Slack app scopes for permissions.
        signing_secret (Optional[str]): Signing secret for verifying Slack
            requests.
        client_id (Optional[str]): Slack app client ID.
        client_secret (Optional[str]): Slack app client secret.
        redirect_uri_path (str): The URI path for OAuth redirect, defaults to
            "/slack/oauth_redirect".
        installation_store (Optional[AsyncInstallationStore]): The installation
            store for handling OAuth installations.
    """

    @dependencies_required('slack_bolt')
    def __init__(
        self,
        token: Optional[str] = None,
        scopes: Optional[str] = None,
        signing_secret: Optional[str] = None,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        redirect_uri_path: str = "/slack/oauth_redirect",
        installation_store: Optional[AsyncInstallationStore] = None,
    ) -> None:
        """Initializes the SlackApp instance by setting up the Slack Bolt app
        and configuring event handlers and OAuth settings.

        Args:
            token (Optional[str]): The Slack API token.
            scopes (Optional[str]): The scopes for Slack app permissions.
            signing_secret (Optional[str]): The signing secret for verifying
                requests.
            client_id (Optional[str]): The Slack app client ID.
            client_secret (Optional[str]): The Slack app client secret.
            redirect_uri_path (str): The URI path for handling OAuth redirects
                (default is "/slack/oauth_redirect").
            installation_store (Optional[AsyncInstallationStore]): An optional
                installation store for OAuth installations.
        """
        from slack_bolt.adapter.starlette.async_handler import (
            AsyncSlackRequestHandler,
        )
        from slack_bolt.app.async_app import AsyncApp
        from slack_bolt.oauth.async_oauth_settings import AsyncOAuthSettings

        self.token: Optional[str] = token or os.getenv("SLACK_TOKEN")
        self.scopes: Optional[str] = scopes or os.getenv("SLACK_SCOPES")
        self.signing_secret: Optional[str] = signing_secret or os.getenv(
            "SLACK_SIGNING_SECRET"
        )
        self.client_id: Optional[str] = client_id or os.getenv(
            "SLACK_CLIENT_ID"
        )
        self.client_secret: Optional[str] = client_secret or os.getenv(
            "SLACK_CLIENT_SECRET"
        )

        if not all([self.token, self.scopes, self.signing_secret]):
            raise ValueError(
                "`SLACK_TOKEN`, `SLACK_SCOPES`, and `SLACK_SIGNING_SECRET` "
                "environment variables must be set. Get it here: "
                "`https://api.slack.com/apps`."
            )

        # Initialize Slack Bolt AsyncApp with settings
        self._app = AsyncApp(
            logger=logger,
            signing_secret=self.signing_secret,
            installation_store=installation_store,
            token=self.token,
        )

        # Setup OAuth settings if client ID and secret are provided
        if client_id and client_secret:
            self._app.oauth_settings = AsyncOAuthSettings(
                client_id=self.client_id,
                client_secret=self.client_secret,
                scopes=self.scopes,
                redirect_uri_path=redirect_uri_path,
            )

        self._handler = AsyncSlackRequestHandler(self._app)
        self.setup_handlers()

    def setup_handlers(self) -> None:
        """Sets up the event handlers for Slack events, such as `app_mention`
        and `message`.

        This method registers the `app_mention` and `on_message` event handlers
        with the Slack Bolt app to respond to Slack events.
        """
        self._app.event("app_mention")(self.app_mention)
        self._app.event("message")(self.on_message)

    def run(
        self,
        port: int = 3000,
        path: str = "/slack/events",
        host: Optional[str] = None,
    ) -> None:
        """Start the Slack Bolt app server to listen for incoming Slack events.

        Args:
            port (int): The port on which the server should run (default is
                3000).
            path (str): The endpoint path for receiving Slack events (default
                is "/slack/events").
            host (Optional[str]): The hostname to bind the server (default is
                None).
        """
        self._app.start(port=port, path=path, host=host)

    def run_in_thread(
        self,
        port: int = 3000,
        path: str = "/slack/events",
        host: Optional[str] = None,
    ) -> None:
        """Start the Slack Bolt app server in a separate thread.

        This is useful when you want to run the server alongside other tasks
        in the main thread.

        Args:
            port (int): The port on which the server should run (default is
                3000).
            path (str): The endpoint path for receiving Slack events (default
                is "/slack/events").
            host (Optional[str]): The hostname to bind the server (default is
                None).
        """
        thread = threading.Thread(target=self.run,  args=(port, path, host))
        thread.start()

    async def handle_request(self, request: Request) -> Response:
        """Handles incoming requests from Slack through the request handler.

        Args:
            request (Request): A Starlette request object representing the
                incoming request.

        Returns:
            The response generated by the Slack Bolt handler.
        """
        return await self._handler.handle(request)

    async def app_mention(
        self,
        context: "AsyncBoltContext",
        client: "AsyncWebClient",
        event: dict,
        body: dict,
        say: "AsyncSay",
    ) -> None:
        """Event handler for `app_mention` events.

        This method is triggered when someone mentions the app in Slack.

        Args:
            context (AsyncBoltContext): The Slack Bolt context for the event.
            client (AsyncWebClient): The Slack Web API client.
            event (dict): The event data for the app mention.
            body (dict): The full request body from Slack.
            say (AsyncSay): A function to send a response back to the channel.
        """
        event_profile = SlackEventProfile(**event)
        event_body = SlackEventBody(**body)

        logger.info("on_message, context: {}".format(context))
        logger.info("on_message, client: {}".format(client))
        logger.info("on_message, event_profile: {}".format(event_profile))
        logger.info("on_message, event_body: {}".format(event_body))
        logger.info("on_message, say: {}".format(say))

    async def on_message(
        self,
        context: "AsyncBoltContext",
        client: "AsyncWebClient",
        event: dict,
        body: dict,
        say: "AsyncSay",
    ) -> None:
        """Event handler for `message` events.

        This method is triggered when the app receives a message in Slack.

        Args:
            context (AsyncBoltContext): The Slack Bolt context for the event.
            client (AsyncWebClient): The Slack Web API client.
            event (dict): The event data for the message.
            body (dict): The full request body from Slack.
            say (AsyncSay): A function to send a response back to the channel.
        """
        await context.ack()

        event_profile = SlackEventProfile(**event)
        event_body = SlackEventBody(**body)

        logger.info("on_message, context: {}".format(context))
        logger.info("on_message, client: {}".format(client))
        logger.info("on_message, event_profile: {}".format(event_profile))
        logger.info("on_message, event_body: {}".format(event_body))
        logger.info("on_message, say: {}".format(say))

        await say("Hello, world!")

    def mention_me(
            self,
            context: "AsyncBoltContext",
            body: SlackEventBody
    ) -> bool:
        """Check if the bot is mentioned in the message.

        Args:
            context (AsyncBoltContext): The Slack Bolt context for the event.
            body (SlackEventBody): The body of the Slack event.

        Returns:
            bool: True if the bot is mentioned in the message, False otherwise.
        """
        message = body.event.text
        bot_user_id = context.bot_user_id
        mention = f"<@{bot_user_id}>"
        return mention in message

