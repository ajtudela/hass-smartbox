import logging

_LOGGER = logging.getLogger(__name__)


# async def test_setup_basic(hass, caplog):
#     dev_1_id = "test_device_id_1"
#     mock_node_1 = mock_node(dev_1_id, 1, HEATER_NODE_TYPE_HTR)
#     mock_node_2 = mock_node(dev_1_id, 2, HEATER_NODE_TYPE_HTR_MOD)
#     mock_dev_1 = mock_device(dev_1_id, [mock_node_1, mock_node_2])

#     # with patch(
#     #     "custom_components.smartbox.model.get_devices",
#     #     autospec=True,
#     #     return_value=[mock_dev_1],
#     # ) as get_devices_mock:
#     entry = MockConfigEntry(
#         domain=DOMAIN,
#         title="test_username_1",
#         data=MOCK_SMARTBOX_CONFIG[DOMAIN],
#     )
#     entry.add_to_hass(hass)
#     assert await hass.config_entries.async_setup(entry.entry_id)
#     await hass.async_block_till_done()
#     # get_devices_mock.assert_any_await(
#     #     hass,

#     # )
#     assert mock_dev_1 in hass.data[DOMAIN][SMARTBOX_DEVICES]

#     assert_log_message(
#         caplog,
#         "custom_components.smartbox",
#         logging.INFO,
#         f"Setting up Smartbox integration v{__version__}"
#         f" (using smartbox v{SMARTBOX_VERSION})",
#     )


# async def test_setup_unsupported_nodes(hass, caplog):
#     dev_1_id = "test_device_id_1"
#     mock_node_1 = mock_node(dev_1_id, 1, HEATER_NODE_TYPE_HTR_MOD)
#     mock_node_2 = mock_node(dev_1_id, 2, "test_unsupported_node")
#     mock_dev_1 = mock_device(dev_1_id, [mock_node_1, mock_node_2])

#     with patch(
#         "custom_components.smartbox.get_devices",
#         autospec=True,
#         return_value=[mock_dev_1],
#     ) as get_devices_mock:
#         assert await async_setup_component(hass, "smartbox", TEST_CONFIG_1)
#         get_devices_mock.assert_any_await(
#             hass,
#             TEST_CONFIG_1[DOMAIN][CONF_ACCOUNTS][0][CONF_API_NAME],
#             TEST_CONFIG_1[DOMAIN][CONF_BASIC_AUTH_CREDS],
#             TEST_CONFIG_1[DOMAIN][CONF_ACCOUNTS][0][CONF_USERNAME],
#             TEST_CONFIG_1[DOMAIN][CONF_ACCOUNTS][0][CONF_PASSWORD],
#             TEST_CONFIG_1[DOMAIN][CONF_ACCOUNTS][0][CONF_SESSION_RETRY_ATTEMPTS],
#             TEST_CONFIG_1[DOMAIN][CONF_ACCOUNTS][0][CONF_SESSION_BACKOFF_FACTOR],
#             TEST_CONFIG_1[DOMAIN][CONF_ACCOUNTS][0][CONF_SOCKET_RECONNECT_ATTEMPTS],
#             TEST_CONFIG_1[DOMAIN][CONF_ACCOUNTS][0][CONF_SOCKET_BACKOFF_FACTOR],
#         )
#     assert_log_message(
#         caplog,
#         "custom_components.smartbox",
#         logging.ERROR,
#         'Nodes of type "test_unsupported_node" are not yet supported; '
#         "no entities will be created. Please file an issue on GitHub.",
#     )
