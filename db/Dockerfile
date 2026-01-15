FROM postgres:15-alpine

# Copy migration files and script
COPY migrations/ /migrations/
COPY migrate.sh /migrate.sh

RUN chmod +x /migrate.sh

ENTRYPOINT ["/migrate.sh"]

