/**
 * The `value` payloads PulseOps puts on Feishu card buttons, and the one
 * parser `card.action.trigger` uses for them. Cards are built in
 * feishu-cards/ and read back in feishu.ts; both sides use these names, so a
 * button and its handler cannot drift apart.
 */

import {
  parseMesWorkOrderValue,
  type MesWorkOrderPayload,
} from './mes-workorder-actions.js';

/** Stops the run the streaming card belongs to. */
export const CARD_ACTION_INTERRUPT_STREAM = 'interrupt_stream';
/**
 * Answers an AskUserQuestion option: the chosen option becomes the clicking
 * user's next message in that chat, exactly as if they had typed it.
 */
export const CARD_ACTION_ANSWER_QUESTION = 'answer_question';
/**
 * Advances a work order from a 设备异常工单 alarm card (接单 / 已处理 / 确认闭环).
 * Those cards are sent by mes-mcp-server, not built here, so only the value
 * shape is shared with it.
 */
export const CARD_ACTION_MES_WORK_ORDER = 'mes_work_order';

/** Longest question/option text carried in a button payload. */
export const CARD_ANSWER_TEXT_LIMIT = 300;

export interface AnswerQuestionPayload {
  action: typeof CARD_ACTION_ANSWER_QUESTION;
  /** The question as shown, so the answer reads on its own in the chat. */
  question: string;
  /** The option label the user picked. */
  answer: string;
}

export type CardActionPayload =
  | { action: typeof CARD_ACTION_INTERRUPT_STREAM }
  | AnswerQuestionPayload
  | MesWorkOrderPayload;

function clip(text: string): string {
  const trimmed = text.trim();
  return trimmed.length > CARD_ANSWER_TEXT_LIMIT
    ? `${trimmed.slice(0, CARD_ANSWER_TEXT_LIMIT - 1)}…`
    : trimmed;
}

/** The button value for one AskUserQuestion option. */
export function answerQuestionValue(
  question: string,
  answer: string,
): AnswerQuestionPayload {
  return {
    action: CARD_ACTION_ANSWER_QUESTION,
    question: clip(question),
    answer: clip(answer),
  };
}

/** Reads a button value back; null for anything PulseOps did not send. */
export function parseCardActionValue(value: unknown): CardActionPayload | null {
  if (!value || typeof value !== 'object') return null;
  const record = value as Record<string, unknown>;
  if (record.action === CARD_ACTION_INTERRUPT_STREAM) {
    return { action: CARD_ACTION_INTERRUPT_STREAM };
  }
  if (
    record.action === CARD_ACTION_ANSWER_QUESTION &&
    typeof record.answer === 'string' &&
    record.answer.trim()
  ) {
    return answerQuestionValue(
      typeof record.question === 'string' ? record.question : '',
      record.answer,
    );
  }
  if (record.action === CARD_ACTION_MES_WORK_ORDER) {
    return parseMesWorkOrderValue(value);
  }
  return null;
}

/**
 * The message text an answer button sends on the user's behalf: the option
 * alone when the question is unknown, otherwise both, so the agent sees which
 * question it answers.
 */
export function answerQuestionMessage(payload: AnswerQuestionPayload): string {
  return payload.question
    ? `${payload.question}\n${payload.answer}`
    : payload.answer;
}
