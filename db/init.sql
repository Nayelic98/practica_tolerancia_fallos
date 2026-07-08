-- Esquema mínimo para el sistema de reservas de entradas.
-- Se comparte entre inventario-service y reservas-service (BD compartida).

CREATE TABLE IF NOT EXISTS inventory (
    event_id         VARCHAR(50) PRIMARY KEY,
    event_name       VARCHAR(200) NOT NULL,
    total_seats      INT NOT NULL,
    available_seats  INT NOT NULL CHECK (available_seats >= 0)
);

CREATE TABLE IF NOT EXISTS reservations (
    id           SERIAL PRIMARY KEY,
    event_id     VARCHAR(50) NOT NULL REFERENCES inventory(event_id),
    user_email   VARCHAR(200) NOT NULL,
    quantity     INT NOT NULL,
    status       VARCHAR(20) NOT NULL, -- CONFIRMED | FAILED
    payment_id   VARCHAR(100),
    notified     BOOLEAN NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMP NOT NULL DEFAULT now(),
    updated_at   TIMESTAMP NOT NULL DEFAULT now()
);

INSERT INTO inventory (event_id, event_name, total_seats, available_seats) VALUES
    ('evt-001', 'Concierto Rock Nacional', 200, 200),
    ('evt-002', 'Final de Copa',           500, 500),
    ('evt-003', 'Obra de Teatro',           80,  80)
ON CONFLICT (event_id) DO NOTHING;
