import os
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

load_dotenv()  # charge automatiquement le fichier .env

def send_email_alert(subject, body):
    sender_email = "blackcypher1652@gmail.com"
    receiver_email = "blockprodproject@gmail.com"
    smtp_server = "smtp.gmail.com"
    smtp_port = 587
    smtp_user = sender_email

    # Récupère le mot de passe depuis la variable d’environnement
    smtp_password = os.getenv('GOOGLE_MAIL_PASSWORD')

    print("[DEBUG] Lecture de la variable d'environnement GOOGLE_MAIL_PASSWORD...")
    if smtp_password is None:
        print("[ERREUR] La variable d’environnement GOOGLE_MAIL_PASSWORD n’est pas définie.")
        return
    else:
        print("[INFO] Variable d’environnement récupérée avec succès.")

    try:
        # Création du message
        msg = MIMEMultipart()
        msg['From'] = sender_email
        msg['To'] = receiver_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        # Connexion au serveur SMTP
        print("[DEBUG] Connexion au serveur SMTP...")
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.ehlo()
        server.starttls()
        server.ehlo()

        print("[DEBUG] Connexion en cours avec les identifiants...")
        server.login(smtp_user, smtp_password)

        print("[DEBUG] Envoi du message...")
        server.sendmail(sender_email, receiver_email, msg.as_string())
        server.quit()
        print("[INFO] E-mail d’alerte envoyé avec succès.")

    except Exception as e:
        print(f"[ERREUR] Impossible d’envoyer l’e-mail : {e}")

# Sujet de l'e-mail
email_subject = " [ALERTE AVA/BTC] Achat exécuté avec succès !"

# Corps de l'e-mail avec mise en forme claire
email_body = f"""
 **ACHAT EXÉCUTÉ !**

Un nouvel ordre d'achat a été réalisé avec succès sur Binance.

 **Détails de l'opération** :
━━━━━━━━━━━━━━━━━━━━━━━━━━
 **Paire**       : #######
 **Montant**     : #######
 **Prix**        : #######
 **Horodatage**  : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

 **Solde spot disponible** :
━━━━━━━━━━━━━━━━━━━━━━━━━━
 **USDC** : #######

━━━━━━━━━━━━━━━━━━━━━━━━━━
 Ceci est un message automatique généré par votre bot de trading Binance.
""".strip()

# Lancer l'envoi
send_email_alert(email_subject, email_body)
