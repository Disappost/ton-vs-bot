import datetime
import os
import random
import re
import traceback
from time import sleep, time

import psycopg2
import telegram

from constants import *
from speaking import *

SHIT_COUNTER = 0
CURRENT_UPDATE_ID = 0
BOT = None
CON = None
CUR = None
MAP_OF_CHANNEL_MESSAGE_ID_AND_USER_ID = {}
QUEUE_OF_MESSAGES_DICT = {}
QUEUE_OF_EDITED_MESSAGES_DICT = {}
LAST_TIME_OF_MESSAGE_FROM_BOT_TO_USER_DICT = {}
LAST_TIME_OF_MESSAGE_FROM_BOT_TO_CHAT_DICT = {
    group_chat_id: 0.0,
    channel_chat_id: 0.0,
    log_chat_id: 0.0
}
LAST_TIME_OF_GREETING_MESSAGE_DICT = {}
LAST_TIME_OF_FLOOD_CONTROL_CAUTION_DICT = {}
separator = '\n\n' + '-' * 64 + '\n\n'


def gaga():
    global SHIT_COUNTER

    while True:
        try:
            main()
            sleep(3)
        except:
            shit_message = 'SHIT...\n' \
                           'SHIT_COUNTER: {}\n' \
                           'time: {}\n' \
                           '\n' \
                           '{}'.format(
                SHIT_COUNTER,
                datetime.datetime.utcnow(),
                traceback.format_exc()
            )
            print(shit_message + separator)
            sleep(2 ** SHIT_COUNTER)
            SHIT_COUNTER += 1


def main():
    start_message = 'start {}'.format(datetime.datetime.utcnow())

    if SHIT_COUNTER:
        start_message += '\n\nSHIT_COUNTER: {}'.format(SHIT_COUNTER)

    print(start_message + separator)

    global BOT
    global CON
    global CUR

    try:
        if heroku:
            bot_token = os.environ['bot_token']
        else:
            bot_token = __import__('gag_secrets').bot_token

        BOT = telegram.Bot(bot_token)

        tg_delay(log_chat_id)
        BOT.send_message(log_chat_id, start_message, disable_notification=True)

        if heroku:
            db_url = os.environ['DATABASE_URL']
            with psycopg2.connect(db_url, sslmode='require') as CON:
                with CON.cursor() as CUR:
                    while True:
                        run()
        else:
            with psycopg2.connect(host='localhost',
                                  database='ton_vs_bot_db_2',
                                  user=__import__('gag_secrets').db_user,
                                  password=__import__('gag_secrets').db_password) as CON:
                with CON.cursor() as CUR:
                    while True:
                        run()

    except:
        my_traceback(1)


def run():
    global CURRENT_UPDATE_ID
    update = None

    try:
        try:
            updates = BOT.get_updates(offset=CURRENT_UPDATE_ID, timeout=60)
        except (telegram.error.TimedOut, telegram.error.Conflict) as exc:
            minor_error(exc)
            return

        for update in updates:
            if not CURRENT_UPDATE_ID:
                CURRENT_UPDATE_ID = update.update_id

            handle_update(update)

            CURRENT_UPDATE_ID = update.update_id + 1

        cleaning()

    except telegram.error.RetryAfter as exc:
        retry_after_message = 'sleep {} seconds, because telegram.error.RetryAfter'.format(exc.retry_after)
        print(retry_after_message + separator)
        sleep(exc.retry_after)
        my_traceback(2, 'telegram.error.RetryAfter')

    except psycopg2.Error:
        CON.rollback()

        my_traceback(2, 'psycopg2.Error', update)
        CURRENT_UPDATE_ID += 1

    except:
        my_traceback(2, None, update)
        CURRENT_UPDATE_ID += 1


def handle_update(update):
    if update.message:
        message = update.message
    elif update.edited_message:
        message = update.edited_message
    else:
        return

    if message.chat.id < 0 and message.chat.id not in LAST_TIME_OF_MESSAGE_FROM_BOT_TO_CHAT_DICT:
        if not message.left_chat_member:
            BOT.leave_chat(message.chat.id)
        return

    if not mute_control(message):
        return

    if not flood_control(update):
        return

    CUR.execute(
        'SELECT 1 FROM updates WHERE update_id = %s',
        [update.update_id]
    )
    query_result = CUR.fetchone()

    if not query_result:
        CUR.execute(
            'INSERT INTO updates (update_id, "update") VALUES (%s, %s)',
            (update.update_id, str(update))
        )
        CON.commit()

    if update.message:
        # channel post
        if message.from_user.id == 777000 and message.sender_chat.id == channel_chat_id:
            channel_post(message)

        # message in group
        elif message.chat.id == group_chat_id:
            message_in_group(message)

        # private message
        elif message.chat.id > 0:
            private_message(message)

    elif update.edited_message and update.edited_message.text:
        handle_edited_message(message)

    CUR.execute(
        'UPDATE updates SET passed = true WHERE update_id = %s',
        [update.update_id]
    )
    CON.commit()


def mute_control(message):
    CUR.execute(
        'SELECT muted_until FROM muted_users WHERE user_id = %s',
        [message.from_user.id]
    )
    query_result = CUR.fetchone()

    if query_result:
        muted_until = query_result[0]

        if muted_until:
            if datetime.datetime.utcnow() > muted_until:
                CUR.execute(
                    'DELETE FROM muted_users WHERE user_id = %s',
                    [message.from_user.id]
                )
                CON.commit()

                update_channel_post(message.from_user)

                return True
            else:
                return False
        else:
            return False
    else:
        return True


def flood_control(update):
    def count(user_id, date, seconds):
        CUR.execute(
            ''' 
                select
                    count(*)
                from
                    flood_control
                where 
                    user_id = %s
                    and "timestamp" < %s 
                    and "timestamp" > %s
            ''',
            (
                user_id,
                date,
                date - datetime.timedelta(seconds=seconds)
            )
        )
        query_result = CUR.fetchone()
        return query_result[0]

    def caution(message, date, reason):
        def send_caution(date):
            add_to_history(
                timestamp=date,
                type='bot_action',
                user_id=message.from_user.id,
                volunteer_id=None,
                column_0='flood_control_caution',
                column_1=reason,
                column_2=None
            )

            date = date.replace(tzinfo=None)

            report_message = 'flood control\n' \
                             'user caution\n' \
                             '\n' \
                             'user_id: {}\n' \
                             'reason: {}\n' \
                             'message time: {}\n' \
                             'bot time: {}'.format(
                message.from_user.id,
                reason,
                date,
                datetime.datetime.utcnow()
            )

            tg_delay(log_chat_id)
            BOT.send_message(log_chat_id, report_message)

            CUR.execute(
                'SELECT group_message_id FROM message_ids WHERE user_id = %s',
                [message.from_user.id]
            )
            query_result = CUR.fetchone()

            if query_result:
                group_message_id = query_result[0]

                message_to_comments = '`❗ Flood control. Caution. {}`'.format(reason)

                tg_delay(group_chat_id)
                BOT.send_message(group_chat_id,
                                 message_to_comments,
                                 reply_to_message_id=group_message_id,
                                 allow_sending_without_reply=True,
                                 parse_mode='MarkdownV2')

            speaking('flood_control_caution', message, reply=True, mono=True)
            LAST_TIME_OF_FLOOD_CONTROL_CAUTION_DICT[message.from_user.id] = time()

        if message.from_user.id in LAST_TIME_OF_FLOOD_CONTROL_CAUTION_DICT:
            time_value = 60 * 5

            if time() - LAST_TIME_OF_FLOOD_CONTROL_CAUTION_DICT[message.from_user.id] > time_value:
                send_caution(date)
        else:
            send_caution(date)

    def mute(message, date, reason):
        CUR.execute(
            'INSERT INTO muted_users (user_id) VALUES (%s)',
            [message.from_user.id]
        )
        CON.commit()

        add_to_history(
            timestamp=date,
            type='bot_action',
            user_id=message.from_user.id,
            volunteer_id=None,
            column_0='mute_user',
            column_1='flood_control',
            column_2=reason
        )

        date = date.replace(tzinfo=None)

        report_message = 'flood control\n' \
                         'mute user\n' \
                         '\n' \
                         'user_id: {}\n' \
                         'reason: {}\n' \
                         'message time: {}\n' \
                         'bot time: {}'.format(
            message.from_user.id,
            reason,
            date,
            datetime.datetime.utcnow()
        )

        tg_delay(log_chat_id)
        BOT.send_message(log_chat_id, report_message)

        update_channel_post(message.from_user)

        CUR.execute(
            'SELECT group_message_id FROM message_ids WHERE user_id = %s',
            [message.from_user.id]
        )
        query_result = CUR.fetchone()

        if query_result:
            group_message_id = query_result[0]

            message_to_comments = '`❗ Flood control. {}. User was muted forever`'.format(reason)

            tg_delay(group_chat_id)
            BOT.send_message(group_chat_id,
                             message_to_comments,
                             reply_to_message_id=group_message_id,
                             allow_sending_without_reply=True,
                             parse_mode='MarkdownV2')

        speaking('flood_control_mute', message, reply=True, mono=True)

    ################

    if update.message:
        message = update.message
        date = update.message.date
    elif update.edited_message:
        message = update.edited_message
        date = update.edited_message.edit_date
    else:
        raise Exception('?')

    if message.from_user.id == 777000:
        return True

    count_of_60_sec = count(message.from_user.id, date, 60)
    if count_of_60_sec > 20:
        mute(message, date, '> 20 messages per 60 sec')
        return False
    elif count_of_60_sec > 13:
        caution(message, date, '> 13 messages per 60 sec')

    count_of_3600_sec = count(message.from_user.id, date, 3600)
    if count_of_3600_sec > 300:
        mute(message, date, '> 300 messages per 3600 sec')
        return False
    elif count_of_3600_sec > 200:
        caution(message, date, '> 200 messages per 3600 sec')

    count_of_86400_sec = count(message.from_user.id, date, 86400)
    if count_of_86400_sec > 1000:
        mute(message, date, '> 1000 messages per 86400 sec')
        return False
    elif count_of_86400_sec > 900:
        caution(message, date, '> 900 messages per 86400 sec')

    CUR.execute(
        'INSERT INTO flood_control ("timestamp", user_id) VALUES (%s, %s)',
        (date, message.from_user.id)
    )
    CON.commit()

    return True


def channel_post(message):
    if message.forward_from_message_id in MAP_OF_CHANNEL_MESSAGE_ID_AND_USER_ID:
        user_id = MAP_OF_CHANNEL_MESSAGE_ID_AND_USER_ID[message.forward_from_message_id]

        CUR.execute(
            'INSERT INTO message_ids (user_id, channel_message_id, group_message_id) VALUES (%s, %s, %s)',
            (
                user_id,
                message.forward_from_message_id,
                message.message_id
            )
        )
        CON.commit()

        queue_of_messages_list = QUEUE_OF_MESSAGES_DICT[user_id].copy()
        queue_of_edited_messages_list = QUEUE_OF_EDITED_MESSAGES_DICT[user_id].copy()

        del QUEUE_OF_EDITED_MESSAGES_DICT[user_id]
        del QUEUE_OF_MESSAGES_DICT[user_id]
        del MAP_OF_CHANNEL_MESSAGE_ID_AND_USER_ID[message.forward_from_message_id]

        for message_from_queue in queue_of_messages_list:
            if message_from_queue.__class__.__name__ == 'NotFirstChannelPostMarker':
                tg_delay(group_chat_id)
                BOT.send_message(group_chat_id,
                                 '`ℹ replied message not found, created new channel post`',
                                 reply_to_message_id=message.message_id,
                                 allow_sending_without_reply=True,
                                 parse_mode='MarkdownV2')
                continue

            my_forward_message(group_chat_id, message_from_queue, message.message_id)

        for edited_message_from_queue in queue_of_edited_messages_list:
            handle_edited_message(edited_message_from_queue)


def message_in_group(message):
    if message.reply_to_message:
        if (message.text and len(message.text) >= 2 and message.text[0:2] == '//') \
                or \
                (message.reply_to_message.text and len(message.reply_to_message.text) >= 2
                 and message.reply_to_message.text[0:2] == '//'):
            return

        if message.text == '/del':
            # add_to_history troubles

            CommandsInGroup.delete_message(message)
            return

        if message.reply_to_message.from_user.id == 777000:
            message_id = message.reply_to_message.message_id

            CUR.execute(
                'SELECT user_id FROM message_ids WHERE group_message_id = %s',
                [message_id]
            )
            query_result = CUR.fetchone()

            if query_result:
                user_id = query_result[0]

                if message.text and message.text[0] == '/':
                    command_in_group(message, user_id)
                    return

                my_forward_message(user_id, message)

                CUR.execute(
                    'UPDATE open_users SET time_of_last_message_by_volunteers = %s WHERE user_id = %s',
                    (message.date, user_id)
                )
                CON.commit()

            else:
                speaking('channel_post_is_inactive', message, reply=True, mono=True)

        else:
            speaking('dont_use_reply_in_comments', message, reply=True, mono=True)

    else:
        speaking('dont_send_messages_outside_comments', message, reply=True, mono=True)


def private_message(message):
    just_opened = False

    if message.text and message.text[0] == '/':
        command_in_pm(message)
        return

    if message.from_user.id in QUEUE_OF_MESSAGES_DICT:
        QUEUE_OF_MESSAGES_DICT[message.from_user.id].append(message)
        return

    # user open status

    CUR.execute(
        'SELECT 1 FROM open_users WHERE user_id = %s',
        [message.from_user.id]
    )
    query_result = CUR.fetchone()

    if not query_result:
        CUR.execute(
            'INSERT INTO open_users (user_id, opening_time) VALUES (%s, %s)',
            (message.from_user.id, message.date)
        )
        CON.commit()

        add_to_history(
            timestamp=message.date,
            type='bot_action',
            user_id=message.from_user.id,
            volunteer_id=None,
            column_0='open_user',
            column_1=None,
            column_2=None
        )

        just_opened = True

        update_channel_post(message.from_user)

        if message.from_user.id in LAST_TIME_OF_GREETING_MESSAGE_DICT:
            time_value = 86400

            if time() - LAST_TIME_OF_GREETING_MESSAGE_DICT[message.from_user.id] > time_value:
                CommandsInPM.start(message)
        else:
            CommandsInPM.start(message)
        LAST_TIME_OF_GREETING_MESSAGE_DICT[message.from_user.id] = time()

    # end of user open status

    # other

    CUR.execute(
        'UPDATE open_users SET time_of_last_message_by_user = %s WHERE user_id = %s',
        (message.date, message.from_user.id)
    )
    CON.commit()

    #

    CUR.execute(
        'SELECT group_message_id FROM message_ids WHERE user_id = %s',
        [message.from_user.id]
    )
    query_result = CUR.fetchone()

    if query_result:
        message_id = query_result[0]

        if just_opened:
            tg_delay(group_chat_id)
            BOT.send_message(group_chat_id,
                             '`🙋 user just opened`',
                             reply_to_message_id=message_id,
                             allow_sending_without_reply=True,
                             parse_mode='MarkdownV2')

        my_forward_message(group_chat_id, message, message_id)

    else:
        create_channel_post(message)


def handle_edited_message(edited_message):
    if edited_message.chat.id in QUEUE_OF_EDITED_MESSAGES_DICT:
        QUEUE_OF_EDITED_MESSAGES_DICT[edited_message.chat.id].append(edited_message)
        return

    # checked edited_message.text exist before

    CUR.execute(
        'SELECT chat_id_1, message_id_1 FROM for_updating_messages WHERE chat_id_0 = %s AND message_id_0 = %s',
        (edited_message.chat.id, edited_message.message_id)
    )
    query_result = CUR.fetchone()

    if query_result:
        try:
            chat_id_1 = query_result[0]
            message_id_1 = query_result[1]

            tg_delay(chat_id_1)
            BOT.edit_message_text(edited_message.text,
                                  chat_id_1,
                                  message_id_1,
                                  entities=edited_message.entities)

            if edited_message.chat.id == group_chat_id:
                user_id = chat_id_1
                volunteer_id = edited_message.from_user.id
            else:
                user_id = edited_message.from_user.id
                volunteer_id = None

            add_to_history(
                timestamp=edited_message.edit_date,
                type='edited_message',
                user_id=user_id,
                volunteer_id=volunteer_id,
                column_0=edited_message.message_id,
                column_1='text',
                column_2=edited_message.text
            )

        except telegram.error.BadRequest as exc:
            if str(exc) == 'Message to edit not found':
                pass
            else:
                raise exc


def command_in_pm(message):
    text = message.text

    add_to_history(
        timestamp=message.date,
        type='message',
        user_id=message.from_user.id,
        volunteer_id=None,
        column_0=message.message_id,
        column_1='text',
        column_2=message.text
    )

    if message.from_user.id == DG_user_id:
        DG_commands(message)

    if text == '/start':
        CommandsInPM.start(message)
        return

    elif bool(re.match('/lang.*', text)):
        CommandsInPM.lang(message)
        return

    if text in ['/l', '/list', '/t', '/taken']:
        volunteers_commands_in_pm(message)
        return


def volunteers_commands_in_pm(message):
    text = message.text

    if is_volunteer(message.from_user):
        if text in ['/l', '/list']:
            CommandsInPM.list(message)
            return

        elif text in ['/t', '/taken']:
            CommandsInPM.taken(message)
            return


class CommandsInPM:
    @staticmethod
    def start(message):
        speaking('greeting_message', message, cut_big_text=True)
        LAST_TIME_OF_GREETING_MESSAGE_DICT[message.from_user.id] = time()

    @staticmethod
    def lang(message):
        text = message.text

        split = re.split('_+', text)

        if len(split) == 1:
            speaking('lang_command', message, reply=True, cut_big_text=True)

        elif len(split) == 2 and split[0] == '/lang' and split[1] in languages_dict:
            enter_lang = split[1]

            if enter_lang != get_user_language(message):

                CUR.execute(
                    'UPDATE languages_of_users SET "language" = %s WHERE user_id = %s',
                    (enter_lang, message.from_user.id)
                )
                CON.commit()

                update_channel_post(message.from_user)

                speaking('language_was_changed', message, reply=True, mono=True)

                speaking('greeting_message', message, cut_big_text=True)

                LAST_TIME_OF_GREETING_MESSAGE_DICT[message.from_user.id] = time()

            else:
                speaking('you_enter_the_same_language', message, reply=True, mono=True)

        else:
            speaking('bad_entered_command', message, reply=True, mono=True)

    @staticmethod
    def list(message):

        CUR.execute(
            '''
                select
                    ou.volunteer_id,
                    ou.user_id,
                    lou."language",
                    ou.opening_time,
                    ou.time_of_last_message_by_user,
                    ou.time_of_last_message_by_volunteers,
                    mi.channel_message_id
                from
                    open_users ou
                left join languages_of_users lou
                on
                    ou.user_id = lou.user_id
                left join message_ids mi 
                on
                    ou.user_id = mi.user_id
                order by
                    ou.volunteer_id is null desc,
                    opening_time
            '''
        )
        query_result = CUR.fetchall()

        response_text = 'list of open users \({}\)'.format(len(query_result))

        for row in query_result:
            volunteer_id = row[0]
            user_id = row[1]
            user_language = row[2]
            opening_time = row[3]
            time_of_last_message_by_user = row[4]
            time_of_last_message_by_volunteers = row[5]
            channel_message_id = row[6]

            text_opening_time = str(datetime.datetime.utcnow().replace(microsecond=0) - opening_time)

            if time_of_last_message_by_user:
                text_time_of_last_message_by_user = str(datetime.datetime.utcnow().replace(microsecond=0)
                                                        - time_of_last_message_by_user)

            else:
                text_time_of_last_message_by_user = 'no messages'

            if time_of_last_message_by_volunteers:
                text_time_of_last_message_by_volunteers = str(datetime.datetime.utcnow().replace(microsecond=0)
                                                              - time_of_last_message_by_volunteers)
            else:
                text_time_of_last_message_by_volunteers = 'no answer'

            candidate_to_response_text = '\n' \
                                         '\n' \
                                         '{} {}\n' \
                                         '    {}\n' \
                                         '        {}\n' \
                                         '            {}\n' \
                                         '                {}\n' \
                                         '                    {}'.format(
                '🟡' if volunteer_id else '🔴',
                escape_markdown(get_tg_user_info(user_id)),
                user_language,
                text_opening_time,
                text_time_of_last_message_by_user,
                text_time_of_last_message_by_volunteers,
                '[link to post]({}/{})'.format(channel_link, channel_message_id)
            )

            if len(response_text + candidate_to_response_text) > 4096:
                break
            else:
                response_text += candidate_to_response_text

        if len(query_result) == 0:
            response_text += '\nempty'

        tg_delay(message.from_user.id)
        BOT.send_message(message.from_user.id,
                         response_text,
                         reply_to_message_id=message.message_id,
                         allow_sending_without_reply=True,
                         parse_mode='MarkdownV2')

    @staticmethod
    def taken(message):
        CUR.execute(
            '''
                select
                    ou.user_id,
                    lou."language",
                    ou.opening_time, 
                    ou.time_of_last_message_by_user,
                    ou.time_of_last_message_by_volunteers,
                    mi.channel_message_id
                from
                    open_users ou
                left join languages_of_users lou
                on
                    ou.user_id = lou.user_id
                left join message_ids mi 
                on
                    ou.user_id = mi.user_id
                where
                    ou.volunteer_id = %s
                order by
                    ou.opening_time
            ''',
            [message.from_user.id]
        )
        query_result = CUR.fetchall()

        response_text = 'list of taken users by you \({}\)'.format(len(query_result))

        for row in query_result:
            user_id = row[0]
            language = row[1]
            opening_time = row[2]
            time_of_last_message_by_user = row[3]
            time_of_last_message_by_volunteers = row[4]
            channel_message_id = row[5]

            text_opening_time = str(datetime.datetime.utcnow().replace(microsecond=0) - opening_time)

            if time_of_last_message_by_user:
                text_time_of_last_message_by_user = str(datetime.datetime.utcnow().replace(microsecond=0)
                                                        - time_of_last_message_by_user)
            else:
                text_time_of_last_message_by_user = 'no message'

            if time_of_last_message_by_volunteers:
                text_time_of_last_message_by_volunteers = str(datetime.datetime.utcnow().replace(microsecond=0)
                                                              - time_of_last_message_by_volunteers)
            else:
                text_time_of_last_message_by_volunteers = 'no answer'

            candidate_to_response_text = '\n' \
                                         '\n' \
                                         '• {}\n' \
                                         '    {}\n' \
                                         '        {}\n' \
                                         '            {}\n' \
                                         '                {}\n' \
                                         '                    {}'.format(
                escape_markdown(get_tg_user_info(user_id)),
                language,
                text_opening_time,
                text_time_of_last_message_by_user,
                text_time_of_last_message_by_volunteers,
                '[link to post]({}/{})'.format(channel_link, channel_message_id)
            )

            if len(response_text + candidate_to_response_text) > 4096:
                break
            else:
                response_text += candidate_to_response_text

        if len(query_result) == 0:
            response_text += '\nempty'

        tg_delay(message.from_user.id)
        BOT.send_message(message.from_user.id,
                         response_text,
                         reply_to_message_id=message.message_id,
                         allow_sending_without_reply=True,
                         parse_mode='MarkdownV2')


def DG_commands(message):
    text = message.text

    if text == '/help':
        DgCommands.help()
        return

    elif text == '/get_shit_counter':
        DgCommands.get_shit_counter()
        return

    elif text == '/reset_shit_counter':
        DgCommands.reset_shit_counter()
        return

    elif text == '/gaga':
        DgCommands.gaga()
        return

    elif bool(re.match('/tables_counts.*', text)):
        DgCommands.tables_counts(message)
        return


class DgCommands:
    @staticmethod
    def help():
        help_text = '/help\n' \
                    '/gaga\n' \
                    '/get_shit_counter\n' \
                    '/reset_shit_counter\n' \
                    '/tables_counts # 1 day\n' \
                    '/tables_counts n # n - days\n'

        tg_delay(DG_user_id)
        BOT.send_message(DG_user_id, truncate_big_text(0, help_text))

    @staticmethod
    def get_shit_counter():
        tg_delay(DG_user_id)
        BOT.send_message(DG_user_id, 'SHIT_COUNTER: {}'.format(SHIT_COUNTER))

    @staticmethod
    def reset_shit_counter():
        global SHIT_COUNTER
        SHIT_COUNTER = 0

        tg_delay(DG_user_id)
        BOT.send_message(DG_user_id, 'mb ok')

    @staticmethod
    def gaga():
        CUR.execute('SELECT COUNT(*) FROM updates WHERE passed = false')
        query_result = CUR.fetchone()
        if query_result:
            problem_updates_count = query_result[0]
        else:
            problem_updates_count = None

        gaga_text = '{}: {}\n' \
                    '{}: {}\n' \
                    '\n' \
                    '{}: {}\n' \
                    '{}: {}\n' \
                    '{}: {}\n' \
                    '\n' \
                    '{}: {}\n' \
                    '{}: {}\n' \
                    '{}: {}\n'.format(
            'SHIT_COUNTER', SHIT_COUNTER,
            'problem_updates_count (n - 1)', problem_updates_count,

            'len(MAP_OF_CHANNEL_MESSAGE_ID_AND_USER_ID)', len(MAP_OF_CHANNEL_MESSAGE_ID_AND_USER_ID),
            'len(QUEUE_OF_MESSAGES_DICT)', len(QUEUE_OF_MESSAGES_DICT),
            'len(QUEUE_OF_EDITED_MESSAGES_DICT)', len(QUEUE_OF_EDITED_MESSAGES_DICT),

            'len(LAST_TIME_OF_MESSAGE_FROM_BOT_TO_USER_DICT)', len(LAST_TIME_OF_MESSAGE_FROM_BOT_TO_USER_DICT),
            'len(LAST_TIME_OF_GREETING_MESSAGE_DICT)', len(LAST_TIME_OF_GREETING_MESSAGE_DICT),
            'len(LAST_TIME_OF_FLOOD_CONTROL_CAUTION_DICT)', len(LAST_TIME_OF_FLOOD_CONTROL_CAUTION_DICT),
        )

        tg_delay(DG_user_id)
        BOT.send_message(DG_user_id, gaga_text)

    @staticmethod
    def tables_counts(message):
        def gaga(days):
            dt = datetime.datetime.utcnow() - datetime.timedelta(days=days)

            CUR.execute('SELECT COUNT(*) FROM flood_control WHERE "timestamp" > %s', [dt])
            query_result = CUR.fetchone()
            if query_result:
                flood_control_count = query_result[0]
            else:
                flood_control_count = None

            CUR.execute('SELECT COUNT(*) FROM history WHERE "timestamp" > %s', [dt])
            query_result = CUR.fetchone()
            if query_result:
                history_count = query_result[0]
            else:
                history_count = None

            CUR.execute('SELECT COUNT(*) FROM updates WHERE "timestamp" > %s', [dt])
            query_result = CUR.fetchone()
            if query_result:
                updates_count = query_result[0]
            else:
                updates_count = None

            report_message = 'days: {}\n' \
                             '\n' \
                             'flood_control_count: {}\n' \
                             'history_count: {}\n' \
                             'updates_count: {}'.format(
                days,
                flood_control_count,
                history_count,
                updates_count
            )

            tg_delay(DG_user_id)
            BOT.send_message(DG_user_id, report_message)

        ####

        split = re.split(' +', message.text)

        if len(split) == 1:
            gaga(1)
        elif len(split) == 2 and split[1].isdigit():
            number_of_days = int(split[1])
            gaga(number_of_days)
        else:
            speaking('bad_entered_command', message, reply=True, mono=True)


def command_in_group(message, user_id):
    text = message.text

    add_to_history(
        timestamp=message.date,
        type='message',
        user_id=user_id,
        volunteer_id=message.from_user.id,
        column_0=message.message_id,
        column_1='text',
        column_2=message.text
    )

    if text in ['/t', '/take']:
        CommandsInGroup.take(message, user_id)
        return

    elif text in ['/d', '/drop']:
        CommandsInGroup.drop(message, user_id)
        return

    elif text in ['/c', '/close']:
        CommandsInGroup.close(message, user_id)
        return

    elif text in ['/o', '/open']:
        CommandsInGroup.open(message, user_id)
        return

    elif bool(re.match('/mute.*', text)):
        CommandsInGroup.mute(message, user_id)
        return

    elif text == '/unmute':
        CommandsInGroup.unmute(message, user_id)
        return

    else:
        speaking('I_dont_know_this_command', message, reply=True, mono=True)


class CommandsInGroup:
    @staticmethod
    def take(message, user_id):
        CUR.execute(
            'SELECT volunteer_id FROM open_users WHERE user_id = %s',
            [user_id]
        )
        query_result = CUR.fetchone()

        if query_result:
            volunteer_id = query_result[0]

            if not volunteer_id:
                CUR.execute(
                    'UPDATE open_users SET volunteer_id = %s WHERE user_id = %s',
                    (message.from_user.id, user_id)
                )
                CON.commit()

                add_to_history(
                    timestamp=message.date,
                    type='bot_action',
                    user_id=user_id,
                    volunteer_id=message.from_user.id,
                    column_0='take_user',
                    column_1=None,
                    column_2=None
                )

                speaking('you_have_taken_this_user', message, reply=True, mono=True)

                update_channel_post(user_id)

            else:
                if message.from_user.id == volunteer_id:
                    speaking('this_user_is_already_taken_by_you', message, reply=True, mono=True)
                else:
                    speaking('this_user_is_already_taken', message, reply=True, mono=True)
        else:
            speaking('this_user_is_not_open', message, reply=True, mono=True)

    @staticmethod
    def drop(message, user_id):
        CUR.execute(
            'SELECT volunteer_id FROM open_users WHERE user_id = %s',
            [user_id]
        )
        query_result = CUR.fetchone()

        if query_result:
            volunteer_id = query_result[0]

            if message.from_user.id == volunteer_id:
                CUR.execute(
                    'UPDATE open_users SET volunteer_id = NULL WHERE user_id = %s',
                    [user_id]
                )
                CON.commit()

                add_to_history(
                    timestamp=message.date,
                    type='bot_action',
                    user_id=user_id,
                    volunteer_id=message.from_user.id,
                    column_0='drop_user',
                    column_1=None,
                    column_2=None
                )

                speaking('you_have_dropped_this_user', message, reply=True, mono=True)

                update_channel_post(user_id)

            else:
                speaking('this_user_is_not_taken_by_you', message, reply=True, mono=True)

        else:
            speaking('this_user_is_not_open', message, reply=True, mono=True)

    @staticmethod
    def close(message, user_id):
        CUR.execute(
            'SELECT volunteer_id FROM open_users WHERE user_id = %s',
            [user_id]
        )
        query_result = CUR.fetchone()

        if query_result:
            volunteer_id = query_result[0]

            if message.from_user.id == volunteer_id:
                CUR.execute(
                    'DELETE FROM open_users WHERE user_id = %s',
                    [user_id]
                )
                CON.commit()

                add_to_history(
                    timestamp=message.date,
                    type='bot_action',
                    user_id=user_id,
                    volunteer_id=message.from_user.id,
                    column_0='close_user',
                    column_1=None,
                    column_2=None
                )

                speaking('user_have_been_closed', message, reply=True, mono=True)

                update_channel_post(user_id)

            else:
                speaking('this_user_is_not_taken_by_you', message, reply=True, mono=True)

        else:
            speaking('this_user_is_not_open', message, reply=True, mono=True)

    @staticmethod
    def open(message, user_id):
        CUR.execute(
            'SELECT 1 FROM open_users WHERE user_id = %s',
            [user_id]
        )
        query_result = CUR.fetchone()

        if not query_result:
            CUR.execute(
                'INSERT INTO open_users (user_id, opening_time) VALUES (%s, %s)',
                (user_id, message.date)
            )
            CON.commit()

            add_to_history(
                timestamp=message.date,
                type='bot_action',
                user_id=user_id,
                volunteer_id=message.from_user.id,
                column_0='open_user',
                column_1=None,
                column_2=None
            )

            speaking('user_have_been_opened', message, reply=True, mono=True)

            update_channel_post(user_id)

        else:
            speaking('user_already_open', message, reply=True, mono=True)

    @staticmethod
    def mute(message, user_id):
        text = message.text

        split = re.split(' +', text)

        if len(split) == 1:
            CUR.execute(
                'SELECT muted_until FROM muted_users WHERE user_id = %s',
                [user_id]
            )
            query_result = CUR.fetchone()

            if not query_result:
                CUR.execute(
                    'INSERT INTO muted_users (user_id) VALUES (%s)',
                    [user_id]
                )
                CON.commit()

                add_to_history(
                    timestamp=message.date,
                    type='bot_action',
                    user_id=user_id,
                    volunteer_id=message.from_user.id,
                    column_0='mute_user',
                    column_1=None,
                    column_2=None
                )

                speaking('user_have_been_muted_forever', message, reply=True, mono=True)

                update_channel_post(user_id)

            else:
                muted_until = query_result[0]

                if muted_until:
                    speaking('user_already_muted_until', message, reply=True, mono=True, add_info=str(muted_until))
                else:
                    speaking('user_already_muted_forever', message, reply=True, mono=True)

        elif len(split) == 2 and split[1].isdigit():
            mute_value = int(split[1])

            if 1 <= mute_value <= 365:
                CUR.execute(
                    'SELECT muted_until FROM muted_users WHERE user_id = %s',
                    [user_id]
                )
                query_result = CUR.fetchone()

                if not query_result:
                    until_datetime = message.date.replace(tzinfo=None) + datetime.timedelta(days=mute_value)

                    CUR.execute(
                        'INSERT INTO muted_users (user_id, muted_until) VALUES (%s, %s)',
                        (user_id, until_datetime)
                    )
                    CON.commit()

                    speaking('user_have_been_muted_until', message, reply=True, mono=True,
                             add_info=str(until_datetime))

                    update_channel_post(user_id)

                    add_to_history(
                        timestamp=message.date,
                        type='bot_action',
                        user_id=user_id,
                        volunteer_id=message.from_user.id,
                        column_0='mute_user',
                        column_1=mute_value,
                        column_2=None
                    )

                else:
                    muted_until = query_result[0]

                    if muted_until:
                        speaking('user_already_muted_until', message, reply=True, mono=True,
                                 add_info=str(muted_until))
                    else:
                        speaking('user_already_muted_forever', message, reply=True, mono=True)

            else:
                speaking('mute_value_must_be_in_range', message, reply=True, mono=True)

        else:
            speaking('bad_entered_command', message, reply=True, mono=True)

    @staticmethod
    def unmute(message, user_id):
        CUR.execute(
            'SELECT 1 FROM muted_users WHERE user_id = %s',
            [user_id]
        )
        query_result = CUR.fetchone()

        if query_result:
            CUR.execute(
                'DELETE FROM muted_users WHERE user_id = %s',
                [user_id]
            )
            CON.commit()

            add_to_history(
                timestamp=message.date,
                type='bot_action',
                user_id=user_id,
                volunteer_id=message.from_user.id,
                column_0='unmute_user',
                column_1=None,
                column_2=None
            )

            speaking('user_have_been_unmuted', message, reply=True, mono=True)

            update_channel_post(user_id)

        else:
            speaking('user_not_muted', message, reply=True, mono=True)

    @staticmethod
    def delete_message(message):
        reply_to_message = message.reply_to_message

        if reply_to_message.from_user.id != 777000:
            CUR.execute(
                'SELECT chat_id_1, message_id_1 FROM for_updating_messages WHERE chat_id_0 = %s AND message_id_0 = %s',
                (reply_to_message.chat.id, reply_to_message.message_id)
            )
            query_result = CUR.fetchone()

            if query_result:
                chat_id = query_result[0]
                message_id = query_result[1]

                try:
                    tg_delay(chat_id)
                    BOT.delete_message(chat_id, message_id)

                    speaking('message_deleted', message, reply=True, mono=True)

                    # add to history

                    column_1 = None
                    column_2 = None

                    if reply_to_message.text:
                        column_1 = 'text'
                        column_2 = reply_to_message.text

                    elif reply_to_message.photo:
                        column_1 = 'photo'
                        column_2 = reply_to_message.photo[-1].file_id

                    elif reply_to_message.sticker:
                        column_1 = 'sticker'
                        column_2 = reply_to_message.sticker.file_id

                    elif reply_to_message.video:
                        column_1 = 'video'
                        column_2 = reply_to_message.video.file_id

                    elif reply_to_message.audio:
                        column_1 = 'audio'
                        column_2 = reply_to_message.audio.file_id

                    elif reply_to_message.voice:
                        column_1 = 'voice'
                        column_2 = reply_to_message.voice.file_id

                    elif reply_to_message.video_note:
                        column_1 = 'video_note'
                        column_2 = reply_to_message.video_note.file_id

                    elif reply_to_message.animation:
                        column_1 = 'animation'
                        column_2 = reply_to_message.animation.file_id

                    elif reply_to_message.document:
                        column_1 = 'document'
                        column_2 = reply_to_message.document.file_id

                    add_to_history(
                        timestamp=message.date,
                        type='deleted_message',
                        user_id=chat_id,
                        volunteer_id=message.from_user.id,
                        column_0=reply_to_message.message_id,
                        column_1=column_1,
                        column_2=column_2
                    )

                except telegram.error.BadRequest as exc:
                    reason = '\n\n' + str(exc)
                    speaking('message_not_deleted_because', message, reply=True, mono=True, add_info=reason)

            else:
                speaking('message_not_deleted_because_not_found_in_bot_db', message, reply=True, mono=True)

        else:
            speaking('use_reply_with_del_command', message, reply=True, mono=True)


def channel_post_text(tg_user):
    CUR.execute(
        'SELECT muted_until FROM muted_users WHERE user_id = %s',
        [tg_user.id]
    )
    query_result = CUR.fetchone()

    if query_result:
        muted = True
        muted_until = query_result[0]
    else:
        muted = False
        muted_until = None

    CUR.execute(
        'SELECT opening_time, volunteer_id FROM open_users WHERE user_id = %s',
        [tg_user.id]
    )
    query_result = CUR.fetchone()

    if query_result:
        opening_time = query_result[0]
        volunteer_id = query_result[1]

        if volunteer_id:
            status_line = '🟡 open {}, taken by {}'.format(opening_time, get_tg_user_info(volunteer_id))
        else:
            status_line = '🔴 open {}, not taken'.format(opening_time)
    else:
        status_line = '🟢 closed'

    language = get_user_language(tg_user)

    text = '{}\n' \
           '\n' \
           '{}\n' \
           '\n' \
           'lang: {}\n' \
           '\n' \
           '{}'.format(
        status_line,
        get_tg_user_info(tg_user),
        language,
        '#user_{}'.format(tg_user.id)
    )

    if muted:
        muted_text = '🚫 user muted'
        if muted_until:
            muted_text += ', until {}'.format(muted_until)
        else:
            muted_text += ', forever'

        text = muted_text + '\n\n' + text

    return text


def create_channel_post(message, not_first=False):
    CUR.execute(
        'SELECT 1 FROM languages_of_users WHERE user_id = %s',
        [message.from_user.id]
    )
    query_result = CUR.fetchone()

    if not query_result:
        CUR.execute(
            'INSERT INTO languages_of_users (user_id, language) VALUES (%s, %s)',
            (
                message.from_user.id,
                tg_languages_dict.get(message.from_user.language_code, 'eng')
            )
        )
        CON.commit()

        add_to_history(
            timestamp=message.date,
            type='bot_action',
            user_id=message.from_user.id,
            volunteer_id=None,
            column_0='new_user',
            column_1=None,
            column_2=None
        )

    CUR.execute(
        'DELETE FROM message_ids WHERE user_id = %s',
        [message.from_user.id]
    )
    CON.commit()

    tg_delay(channel_chat_id)
    sent_message = BOT.send_message(channel_chat_id, channel_post_text(message.from_user))

    MAP_OF_CHANNEL_MESSAGE_ID_AND_USER_ID[sent_message.message_id] = message.from_user.id
    QUEUE_OF_MESSAGES_DICT[message.from_user.id] = [message]
    QUEUE_OF_EDITED_MESSAGES_DICT[message.from_user.id] = []

    if not_first:
        class NotFirstChannelPostMarker:
            pass

        marker = NotFirstChannelPostMarker()
        QUEUE_OF_MESSAGES_DICT[message.from_user.id].insert(0, marker)


def update_channel_post(tg_user_or_user_id):
    if isinstance(tg_user_or_user_id, telegram.user.User):
        tg_user = tg_user_or_user_id
        user_id = tg_user.id
    else:
        user_id = tg_user_or_user_id
        tg_user = BOT.get_chat_member(user_id, user_id).user  # if bot is blocked by user - all fine

    CUR.execute(
        'SELECT channel_message_id FROM message_ids WHERE user_id = %s',
        [user_id]
    )
    query_result = CUR.fetchone()

    if query_result:
        channel_message_id = query_result[0]

        text = channel_post_text(tg_user)

        try:
            tg_delay(channel_chat_id)
            BOT.edit_message_text(text, channel_chat_id, channel_message_id)

        except telegram.error.BadRequest as exc:
            error_0_text = 'Message is not modified: ' \
                           'specified new message content and reply markup are exactly the same ' \
                           'as a current content and reply markup of the message'
            error_1_text = 'Message_id_invalid'
            error_2_text = 'Message to edit not found'

            if str(exc) in [error_0_text, error_1_text, error_2_text]:
                report_text = 'update_channel_post\n' \
                              'post_id: {}\n' \
                              '\n' \
                              '{}\n' \
                              '\n' \
                              '{}'.format(channel_message_id, str(exc), datetime.datetime.utcnow())

                print(report_text + separator)

                tg_delay(log_chat_id)
                BOT.send_message(log_chat_id, report_text)

            else:
                raise exc


def tg_delay(target_chat_id):
    if tg_delays:
        if target_chat_id in LAST_TIME_OF_MESSAGE_FROM_BOT_TO_CHAT_DICT:
            delay_value = 3

            time_difference = time() - LAST_TIME_OF_MESSAGE_FROM_BOT_TO_CHAT_DICT[target_chat_id]
            if time_difference < delay_value:
                sleep(delay_value - time_difference)

            LAST_TIME_OF_MESSAGE_FROM_BOT_TO_CHAT_DICT[target_chat_id] = time()

        elif target_chat_id > 0:
            if target_chat_id in LAST_TIME_OF_MESSAGE_FROM_BOT_TO_USER_DICT:

                delay_value = 1

                time_difference = time() - LAST_TIME_OF_MESSAGE_FROM_BOT_TO_USER_DICT[target_chat_id]
                if time_difference < delay_value:
                    sleep(delay_value - time_difference)

            LAST_TIME_OF_MESSAGE_FROM_BOT_TO_USER_DICT[target_chat_id] = time()


def my_forward_message(to_chat, message, reply_to_message_id=None):
    sent_message = None
    history_column_1 = None
    history_column_2 = None

    try:
        tg_delay(to_chat)

        if message.text \
                and message.via_bot and message.via_bot.id in tg_wallet_bots_dict:
            history_column_1 = tg_wallet_bots_dict[message.via_bot.id]
            history_column_2 = message.text

            sent_message = BOT.send_message(to_chat,
                                            '{}: {}'.format(tg_wallet_bots_dict[message.via_bot.id], message.text),
                                            reply_to_message_id=reply_to_message_id,
                                            reply_markup=message.reply_markup)

        else:
            if message.text:
                if bool(re.match('test_error.*', message.text)):
                    test_error(message)

            sent_message = BOT.copy_message(to_chat,
                                            message.chat.id,
                                            message.message_id,
                                            reply_to_message_id=reply_to_message_id)

            if message.text:
                history_column_1 = 'text'
                history_column_2 = message.text

            elif message.photo:
                history_column_1 = 'photo'
                history_column_2 = message.photo[-1].file_id

            elif message.sticker:
                history_column_1 = 'sticker'
                history_column_2 = message.sticker.file_id

            elif message.video:
                history_column_1 = 'video'
                history_column_2 = message.video.file_id

            elif message.audio:
                history_column_1 = 'audio'
                history_column_2 = message.audio.file_id

            elif message.voice:
                history_column_1 = 'voice'
                history_column_2 = message.voice.file_id

            elif message.video_note:
                history_column_1 = 'video_note'
                history_column_2 = message.video_note.file_id

            elif message.animation:
                history_column_1 = 'animation'
                history_column_2 = message.animation.file_id

            elif message.document:
                history_column_1 = 'document'
                history_column_2 = message.document.file_id

            else:
                history_column_1 = 'other_message_type'
                history_column_2 = None

    except telegram.error.BadRequest as exc:
        if str(exc) == 'Replied message not found':
            create_channel_post(message, not_first=True)

            report_message = 'replied message not found, created new channel post\n' \
                             'old group_message_id: {}\n' \
                             '{}'.format(reply_to_message_id, datetime.datetime.utcnow())

            print(report_message + separator)

            tg_delay(log_chat_id)
            BOT.send_message(log_chat_id, report_message)

        else:
            raise exc

    except telegram.error.Unauthorized as exc:
        if str(exc) == 'Forbidden: bot was blocked by the user':
            speaking('bot_was_blocked_by_the_user', message, reply=True, mono=True)
        else:
            raise exc

    if to_chat == group_chat_id:
        user_id = message.from_user.id
        volunteer_id = None
    elif to_chat > 0:
        user_id = to_chat
        volunteer_id = message.from_user.id
    else:
        raise Exception('?')

    if sent_message:
        CUR.execute(
            '''
                insert into for_updating_messages (
                    "timestamp",
                    chat_id_0,
                    message_id_0,
                    chat_id_1,
                    message_id_1
                )
                values (%s, %s, %s, %s, %s)
            ''',
            (
                message.date,
                message.chat.id,
                message.message_id,
                to_chat,
                sent_message.message_id
            )
        )
        CON.commit()

    add_to_history(
        timestamp=message.date,
        type='message',
        user_id=user_id,
        volunteer_id=volunteer_id,
        column_0=message.message_id,
        column_1=history_column_1,
        column_2=history_column_2
    )


def speaking(speaking_keyword, message, reply=False, mono=False, cut_big_text=False, add_info=None):
    # ! don't use mono=True and cut_bit_text=True together

    user_language = get_user_language(message)

    if speaking_keyword in speaking_dict:
        dict_of_phrase = speaking_dict[speaking_keyword]

        if user_language in dict_of_phrase:
            answer = dict_of_phrase[user_language]

        else:
            answer = dict_of_phrase.get('eng', None)

    else:
        answer = None

    if answer:
        if reply:
            reply_to_message_id = message.message_id
        else:
            reply_to_message_id = None

        if add_info:
            answer = answer + add_info

        if mono:
            answer = '`{}`'.format(answer)
            parse_mode = 'MarkdownV2'
        else:
            parse_mode = None

        if cut_big_text:
            answer = truncate_big_text(0, answer)

        tg_delay(message.chat.id)
        BOT.send_message(message.chat.id,
                         answer,
                         reply_to_message_id=reply_to_message_id,
                         allow_sending_without_reply=True,
                         parse_mode=parse_mode)

    else:
        report_message = 'no answer in speaking()\n' \
                         '\n' \
                         'answer_keyword: "{}"\n' \
                         'user_language: {}\n' \
                         'user_id: {}\n' \
                         '\n' \
                         'message time: {}\n' \
                         'bot time: {}'.format(
            speaking_keyword,
            user_language,
            message.from_user.id,
            message.date,
            datetime.datetime.utcnow()
        )

        tg_delay(log_chat_id)
        BOT.send_message(log_chat_id, report_message)


def get_tg_user_info(tg_user_or_user_id):
    if isinstance(tg_user_or_user_id, telegram.user.User):
        tg_user = tg_user_or_user_id
    else:
        user_id = tg_user_or_user_id
        tg_user = BOT.get_chat_member(user_id, user_id).user  # if bot is blocked by user - all fine

    text = ''

    if tg_user.first_name:
        text += tg_user.first_name

    if tg_user.last_name:
        text += ' ' + tg_user.last_name

    if tg_user.username:
        text += ' @' + tg_user.username

    return text


def get_user_language(tg_message_or_user):
    if isinstance(tg_message_or_user, telegram.Message):
        message = tg_message_or_user
        tg_user = message.from_user
    elif isinstance(tg_message_or_user, telegram.User):
        message = None
        tg_user = tg_message_or_user
    else:
        raise Exception('?')

    CUR.execute(
        'SELECT language FROM languages_of_users WHERE user_id = %s',
        [tg_user.id]
    )
    query_result = CUR.fetchone()

    if query_result:
        language = query_result[0]

    else:
        language = tg_languages_dict.get(tg_user.language_code, 'eng')

        if message:
            CUR.execute(
                'INSERT INTO languages_of_users (user_id, language) VALUES (%s, %s)',
                (message.from_user.id, language)
            )
            CON.commit()

            add_to_history(
                timestamp=message.date,
                type='bot_action',
                user_id=message.from_user.id,
                volunteer_id=None,
                column_0='new_user',
                column_1=None,
                column_2=None
            )

    return language


def is_volunteer(tg_user):
    try:
        status = BOT.get_chat_member(channel_chat_id, tg_user.id).status
    except telegram.error.BadRequest as exc:
        if str(exc) == 'User not found':
            status = 'never was member'
        else:
            raise exc

    if status in ['creator', 'administrator', 'member']:
        return True
    else:
        return False


def truncate_big_text(mode, text):
    max_allowed_text_length = 4096

    if len(text) > max_allowed_text_length:
        delimiter = '......'

        if mode:
            text = delimiter + text[len(text) - (max_allowed_text_length - len(delimiter)):len(text)]
        else:
            text = text[0:max_allowed_text_length - len(delimiter)] + delimiter

    return text


def escape_markdown(str):
    characters_list = ['\\', '_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']

    for char in characters_list:
        str = str.replace(char, '\\' + char)

    return str


def add_to_history(**kwargs):
    CUR.execute(
        '''
            insert into history (
                "timestamp",
                "type",
                user_id,
                volunteer_id,
                column_0,
                column_1,
                column_2
            )
            values (%s, %s, %s, %s, %s, %s, %s)
        ''',
        (
            kwargs['timestamp'],
            kwargs['type'],
            kwargs['user_id'],
            kwargs['volunteer_id'],
            kwargs['column_0'],
            kwargs['column_1'],
            kwargs['column_2']
        )
    )
    CON.commit()


def cleaning():
    random_value = 0.01

    if random.random() < random_value:
        choice = random.choice(range(6))

        if choice == 0:
            Cleaning.clean_LAST_TIME_OF_MESSAGE_FROM_BOT_TO_USER_DICT()
        elif choice == 1:
            Cleaning.clean_LAST_TIME_OF_GREETING_MESSAGE_DICT()
        elif choice == 2:
            Cleaning.clean_LAST_TIME_OF_FLOOD_CONTROL_CAUTION_DICT()
        elif choice == 3:
            Cleaning.clean_for_updating_messages_table()
        elif choice == 4:
            Cleaning.clean_updates_table()
        elif choice == 5:
            Cleaning.clean_flood_control_table()


class Cleaning:
    @staticmethod
    def clean_LAST_TIME_OF_MESSAGE_FROM_BOT_TO_USER_DICT():
        live_time_value = 86400

        current_time = time()

        for dict_key in LAST_TIME_OF_MESSAGE_FROM_BOT_TO_USER_DICT.copy():
            if current_time - LAST_TIME_OF_MESSAGE_FROM_BOT_TO_USER_DICT[dict_key] > live_time_value:
                del LAST_TIME_OF_MESSAGE_FROM_BOT_TO_USER_DICT[dict_key]

        add_to_history(
            timestamp=datetime.datetime.utcnow(),
            type='bot_action',
            user_id=None,
            volunteer_id=None,
            column_0='cleaning',
            column_1='LAST_TIME_OF_MESSAGE_FROM_BOT_TO_USER_DICT',
            column_2=None
        )

    @staticmethod
    def clean_LAST_TIME_OF_GREETING_MESSAGE_DICT():
        live_time_value = 86400

        current_time = time()

        for dict_key in LAST_TIME_OF_GREETING_MESSAGE_DICT.copy():
            if current_time - LAST_TIME_OF_GREETING_MESSAGE_DICT[dict_key] > live_time_value:
                del LAST_TIME_OF_GREETING_MESSAGE_DICT[dict_key]

        add_to_history(
            timestamp=datetime.datetime.utcnow(),
            type='bot_action',
            user_id=None,
            volunteer_id=None,
            column_0='cleaning',
            column_1='LAST_TIME_OF_GREETING_MESSAGE_DICT',
            column_2=None
        )

    @staticmethod
    def clean_LAST_TIME_OF_FLOOD_CONTROL_CAUTION_DICT():
        live_time_value = 86400

        current_time = time()

        for dict_key in LAST_TIME_OF_FLOOD_CONTROL_CAUTION_DICT.copy():
            if current_time - LAST_TIME_OF_FLOOD_CONTROL_CAUTION_DICT[dict_key] > live_time_value:
                del LAST_TIME_OF_FLOOD_CONTROL_CAUTION_DICT[dict_key]

        add_to_history(
            timestamp=datetime.datetime.utcnow(),
            type='bot_action',
            user_id=None,
            volunteer_id=None,
            column_0='cleaning',
            column_1='LAST_TIME_OF_FLOOD_CONTROL_CAUTION_DICT',
            column_2=None
        )

    @staticmethod
    def clean_for_updating_messages_table():
        dt = datetime.datetime.utcnow() - datetime.timedelta(days=30)

        CUR.execute(
            'DELETE FROM for_updating_messages WHERE "timestamp" < %s',
            [dt]
        )
        CON.commit()

        add_to_history(
            timestamp=datetime.datetime.utcnow(),
            type='bot_action',
            user_id=None,
            volunteer_id=None,
            column_0='cleaning',
            column_1='for_updating_messages_table',
            column_2=None
        )

    @staticmethod
    def clean_updates_table():
        dt = datetime.datetime.utcnow() - datetime.timedelta(days=30)

        CUR.execute(
            'DELETE FROM updates WHERE "timestamp" < %s',
            [dt]
        )
        CON.commit()

        add_to_history(
            timestamp=datetime.datetime.utcnow(),
            type='bot_action',
            user_id=None,
            volunteer_id=None,
            column_0='cleaning',
            column_1='updates_table',
            column_2=None
        )

    @staticmethod
    def clean_flood_control_table():
        dt = datetime.datetime.utcnow() - datetime.timedelta(days=2)

        CUR.execute(
            'DELETE FROM flood_control WHERE "timestamp" < %s',
            [dt]
        )
        CON.commit()

        add_to_history(
            timestamp=datetime.datetime.utcnow(),
            type='bot_action',
            user_id=None,
            volunteer_id=None,
            column_0='cleaning',
            column_1='flood_control_table',
            column_2=None
        )


def test_error(message):
    def error_0():
        raise Exception('gaga test_error')

    def error_1():
        CUR.execute('INSERT INTO languages_of_users VALUES (1168253329, \'eng\')')
        CON.commit()

    def error_2():
        BOT.send_message(DG_user_id, 'a' * 4097)

    ################

    if not message.text:
        return

    if not bool(re.match('test_error.*', message.text)):
        return

    if not is_volunteer(message.from_user):
        return

    text = message.text

    split = re.split(' +', text)

    if len(split) == 2 and split[1].isdigit():
        number = int(split[1])

        if number == 0:
            error_0()

        elif number == 1:
            error_1()

        elif number == 2:
            error_2()

    else:
        error_0()


def minor_error(exc):
    message = 'minor error\n' \
              '{}: {}\n' \
              '{}'.format(
        exc.__class__.__name__,
        str(exc),
        datetime.datetime.utcnow()
    )

    print(message + separator)

    tg_delay(log_chat_id)
    BOT.send_message(log_chat_id, message, disable_notification=True)

    sleep(3)


def my_traceback(level, additional_information=None, update=None):
    global SHIT_COUNTER

    info_message = 'level {}'.format(level)

    if SHIT_COUNTER:
        info_message += '\n\nSHIT_COUNTER: {}'.format(SHIT_COUNTER)

    info_message += '\n\n{}'.format(datetime.datetime.utcnow())

    if additional_information:
        info_message += '\n\n{}'.format(additional_information)

    traceback_message = traceback.format_exc()

    print(info_message + '\n\n' + traceback_message + separator)

    try:
        tg_delay(log_chat_id)
        BOT.send_message(log_chat_id, truncate_big_text(0, info_message))

        tg_delay(log_chat_id)
        BOT.send_message(log_chat_id, truncate_big_text(1, traceback_message))

        if update and update.message:
            problem_message = 'problem message\n\n'

            problem_message += 'chat_id: {}\n'.format(update.message.chat.id)
            problem_message += 'message_id: {}\n'.format(update.message.message_id)
            if update.message.from_user:
                problem_message += 'user_id: {}\n'.format(update.message.from_user.id)

            speaking('mistake', update.message, reply=True, mono=True)

            tg_delay(log_chat_id)
            BOT.send_message(log_chat_id, truncate_big_text(0, problem_message))

            tg_delay(log_chat_id)
            BOT.forward_message(log_chat_id, update.message.chat.id, update.message.message_id)

    except:
        exception_in_my_traceback_message = '!!! exception in handling my_traceback))\n\n'
        exception_in_my_traceback_message += traceback.format_exc()

        print(exception_in_my_traceback_message + separator)

    sleep(2 ** SHIT_COUNTER)
    SHIT_COUNTER += 1


gaga()
