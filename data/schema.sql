-- Scraped data lives in one row per contact; views project records for agent tools.
-- UUIDs are assigned by Python; no extension or superuser permissions required.

CREATE TABLE IF NOT EXISTS agent_runs (
    id UUID NOT NULL,
    query TEXT NOT NULL,
    intent VARCHAR(40),
    answer TEXT,
    status VARCHAR(20) NOT NULL,
    tool_calls JSONB DEFAULT '[]' NOT NULL,
    iterations INTEGER DEFAULT '0' NOT NULL,
    retry_count INTEGER DEFAULT '0' NOT NULL,
    prompt_tokens INTEGER DEFAULT '0' NOT NULL,
    completion_tokens INTEGER DEFAULT '0' NOT NULL,
    latency_ms INTEGER,
    judge_score FLOAT,
    error_message TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS whatsapp_contacts (
    id UUID NOT NULL,
    whatsapp_id VARCHAR(255) NOT NULL,
    phone_number VARCHAR(32),
    contact_name VARCHAR(255),
    is_group BOOLEAN DEFAULT 'false' NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    recent_messages JSONB DEFAULT '[]' NOT NULL CHECK (jsonb_typeof(recent_messages) = 'array'),
    conversation_id UUID,
    unread_count INTEGER DEFAULT '0' NOT NULL,
    last_message_at TIMESTAMP WITH TIME ZONE,
    last_scraped_at TIMESTAMP WITH TIME ZONE,
    category VARCHAR(40),
    category_confidence FLOAT,
    category_reason TEXT,
    action VARCHAR(20),
    classified_at TIMESTAMP WITH TIME ZONE,
    PRIMARY KEY (id),
    UNIQUE (whatsapp_id),
    UNIQUE (conversation_id)
);

CREATE TABLE IF NOT EXISTS evaluation_runs (
    id UUID NOT NULL,
    status VARCHAR(20) NOT NULL,
    dataset VARCHAR(255),
    total_questions INTEGER DEFAULT '0' NOT NULL,
    metrics JSONB DEFAULT '{}' NOT NULL,
    error_message TEXT,
    started_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    completed_at TIMESTAMP WITH TIME ZONE,
    PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS scrape_runs (
    id UUID NOT NULL,
    status VARCHAR(30) NOT NULL,
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    extraction_method VARCHAR(20),
    contacts_found INTEGER DEFAULT '0' NOT NULL,
    messages_found INTEGER DEFAULT '0' NOT NULL,
    contacts_saved INTEGER DEFAULT '0' NOT NULL,
    messages_saved INTEGER DEFAULT '0' NOT NULL,
    messages_pruned INTEGER DEFAULT '0' NOT NULL,
    invalid_records INTEGER DEFAULT '0' NOT NULL,
    duration_ms INTEGER,
    error_message TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS evaluations (
    id UUID NOT NULL,
    evaluation_run_id UUID,
    agent_run_id UUID,
    question TEXT NOT NULL,
    expected TEXT,
    expected_tools JSONB,
    tools_used JSONB DEFAULT '[]' NOT NULL,
    answer TEXT,
    correctness FLOAT,
    relevance FLOAT,
    groundedness FLOAT,
    completeness FLOAT,
    hallucination FLOAT,
    tool_accuracy FLOAT,
    schema_compliant BOOLEAN DEFAULT 'true' NOT NULL,
    final_score FLOAT,
    reasoning TEXT,
    latency_ms INTEGER,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(evaluation_run_id) REFERENCES evaluation_runs (id) ON DELETE CASCADE,
    FOREIGN KEY(agent_run_id) REFERENCES agent_runs (id) ON DELETE SET NULL
);

DO $migration$
BEGIN
    IF to_regclass('contacts') IS NOT NULL AND to_regclass('conversations') IS NOT NULL
       AND to_regclass('messages') IS NOT NULL THEN
        LOCK TABLE contacts, conversations, messages IN SHARE MODE;
        INSERT INTO whatsapp_contacts (
            id, whatsapp_id, phone_number, contact_name, is_group, created_at, updated_at,
            recent_messages, conversation_id, unread_count, last_message_at, last_scraped_at,
            category, category_confidence, category_reason, action, classified_at
        )
        SELECT c.id, c.whatsapp_id, c.phone_number, c.contact_name, c.is_group, c.created_at, c.updated_at,
               coalesce((
                   SELECT jsonb_agg(to_jsonb(m) - 'contact_id'
                                    ORDER BY m.message_timestamp DESC, m.scraped_at DESC, m.id DESC)
                   FROM messages m WHERE m.contact_id = c.id
               ), '[]'::jsonb),
               v.id, coalesce(v.unread_count, 0), v.last_message_at, v.last_scraped_at,
               v.category, v.category_confidence, v.category_reason, v.action, v.classified_at
        FROM contacts c LEFT JOIN conversations v ON v.contact_id = c.id
        ON CONFLICT DO NOTHING;
    END IF;
END
$migration$;

CREATE OR REPLACE VIEW contact_records AS
SELECT id, whatsapp_id, phone_number, contact_name, is_group, created_at, updated_at
FROM whatsapp_contacts;

CREATE OR REPLACE VIEW conversation_records AS
SELECT conversation_id AS id, id AS contact_id, unread_count, last_message_at, last_scraped_at,
       category, category_confidence, category_reason, action, classified_at
FROM whatsapp_contacts WHERE conversation_id IS NOT NULL;

CREATE OR REPLACE VIEW message_records AS
SELECT m.*, c.id AS contact_id
FROM whatsapp_contacts c
CROSS JOIN LATERAL jsonb_to_recordset(c.recent_messages) AS m (
    id UUID, whatsapp_message_id VARCHAR(255), sender_type VARCHAR(20), sender_name VARCHAR(255),
    message_type VARCHAR(30), message_text TEXT, message_timestamp TIMESTAMP WITH TIME ZONE,
    scraped_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX IF NOT EXISTS idx_whatsapp_contacts_last_message ON whatsapp_contacts (last_message_at DESC);
