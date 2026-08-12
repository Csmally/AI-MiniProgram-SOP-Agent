import { motion, AnimatePresence } from 'framer-motion';

export default function Modal({ open, title, message, onConfirm, onCancel, confirmText = '确认', danger = false }) {
  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="modal-overlay"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          onClick={onCancel}
        >
          <motion.div
            className="modal-content"
            initial={{ opacity: 0, scale: 0.95, y: 20 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95, y: 20 }}
            transition={{ type: 'spring', duration: 0.3 }}
            onClick={e => e.stopPropagation()}
          >
            {title && <h3>{title}</h3>}
            {message && <p>{message}</p>}
            <div className="modal-actions">
              <button className="btn-secondary" onClick={onCancel}>取消</button>
              <button className={`btn-primary ${danger ? 'btn-danger-fill' : ''}`} onClick={onConfirm}>
                {confirmText}
              </button>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
